"""Contract tests for ``reusable-bootstrap-repo.yml``.

Phase 5 §5.4 workflow that promotes a consumer repo from ``shadow``
to ``absolute`` / ``ratchet`` once 3 consecutive green CI runs land.
Pins the inputs, env-var indirection (blocks Semgrep CWE-78), and
the conditional PR-creation step so future edits can't silently
drop either guarantee.
"""

from __future__ import absolute_import

import re
import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]


_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "reusable-bootstrap-repo.yml"
)


class ReusableBootstrapRepoWorkflowTests(unittest.TestCase):
    """Invariants on the reusable-bootstrap-repo workflow."""

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the YAML once per test class."""
        cls.text = _WORKFLOW.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)

    def test_workflow_is_workflow_dispatch(self) -> None:
        """Onboarding is manually triggered from the platform repo."""
        # ``on:`` parses as YAML boolean True — access via that key.
        self.assertIn("workflow_dispatch", self.doc[True])

    def test_required_inputs_are_declared(self) -> None:
        """Every field the Python planner reads must be a declared input."""
        inputs = self.doc[True]["workflow_dispatch"]["inputs"]
        for required in ("repo_slug", "profile_path", "target_phase"):
            with self.subTest(input=required):
                self.assertIn(required, inputs)
                self.assertTrue(inputs[required].get("required"))

    def test_target_phase_limited_to_ratchet_or_absolute(self) -> None:
        """``shadow`` is the starting state — cannot promote back to it."""
        inputs = self.doc[True]["workflow_dispatch"]["inputs"]
        options = inputs["target_phase"].get("options", [])
        self.assertEqual(sorted(options), ["absolute", "ratchet"])

    def test_env_var_indirection_for_inputs(self) -> None:
        """``${{ inputs.* }}`` is routed through env vars, not shell args."""
        # All ${{ inputs.X }} usage in the body must be inside an ``env:`` block,
        # never interpolated directly into a ``run: |`` step (Semgrep CWE-78).
        for needle in ("${{ inputs.repo_slug }}", "${{ inputs.profile_path }}"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.text)
        # None of those interpolations should sit inside a python heredoc body —
        # they should only appear on ``env:`` lines or on top-level step fields
        # (``if:``, ``run:`` shell lines that reference env vars, etc.).
        heredoc_bodies = re.findall(
            r"<<'PY'(.+?)PY", self.text, flags=re.DOTALL,
        )
        for body in heredoc_bodies:
            with self.subTest(body_excerpt=body[:60]):
                self.assertNotIn("${{ inputs.", body)
                self.assertNotIn("${{ github.", body)

    def test_promotion_pr_step_gated_on_ready_output(self) -> None:
        """Only open a PR when ``steps.plan.outputs.ready == 'true'``."""
        promote_job = self.doc["jobs"]["promote"]
        steps = promote_job["steps"]
        gated_steps = [
            s for s in steps if "if" in s and "steps.plan.outputs.ready" in s["if"]
        ]
        self.assertTrue(
            gated_steps,
            "No promotion PR step gated on 'ready' — the workflow would "
            "open a PR every run, even when fewer than 3 greens accumulated.",
        )

    def test_concurrency_key_binds_to_slug(self) -> None:
        """Prevent racing promotions on the same repo."""
        concurrency = self.doc.get("concurrency", {})
        self.assertIn("${{ inputs.repo_slug }}", str(concurrency.get("group", "")))

    def test_permissions_are_narrow(self) -> None:
        """Top-level perms empty; job perms limited to contents + pull-requests."""
        self.assertEqual(self.doc.get("permissions"), {})
        promote_job = self.doc["jobs"]["promote"]
        perms = promote_job.get("permissions", {})
        self.assertEqual(perms.get("contents"), "write")
        self.assertEqual(perms.get("pull-requests"), "write")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
