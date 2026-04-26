"""Contract tests for ``reusable-bump-apply.yml``.

Pins the cross-repo invariants for the per-repo bump applier:

* Reusable workflow shape (workflow_call only).
* dry_run defaults to true; real PR-opening requires (a) opt-out
  of dry-run, (b) PAT present, (c) >0 replacements.
* PYTHONPATH wiring so heredoc imports of ``scripts.quality.bumps``
  resolve against the ``platform/`` checkout.
* Artifact name uses slash-escaped slug.
* Env-var indirection for every ``${{ ... }}`` inside heredocs.
"""

from __future__ import absolute_import

import re
import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]


_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "reusable-bump-apply.yml"
)


class ReusableBumpApplyContract(unittest.TestCase):
    """Per-repo bump applier invariants."""

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the YAML once per test class."""
        cls.text = _WORKFLOW.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)

    def test_workflow_call_only(self) -> None:
        """Reusable, never directly dispatched."""
        on = self.doc[True]
        self.assertIn("workflow_call", on)
        self.assertNotIn("workflow_dispatch", on)
        self.assertNotIn("schedule", on)

    def test_required_inputs(self) -> None:
        """``repo_slug`` + ``recipe_path`` are mandatory."""
        inputs = self.doc[True]["workflow_call"]["inputs"]
        for required in ("repo_slug", "recipe_path"):
            with self.subTest(input=required):
                self.assertIn(required, inputs)
                self.assertTrue(inputs[required].get("required"))

    def test_dry_run_default_true(self) -> None:
        """Safety net: operator must opt out of dry-run explicitly."""
        inputs = self.doc[True]["workflow_call"]["inputs"]
        self.assertTrue(inputs["dry_run"].get("default", False))

    def test_drift_sync_pat_secret_optional(self) -> None:
        """PAT is optional — its absence falls through to dry-run."""
        secrets = self.doc[True]["workflow_call"].get("secrets", {})
        self.assertIn("DRIFT_SYNC_PAT", secrets)
        # ``required: false`` means the workflow runs without the
        # PAT and the commit step's ``if`` guard skips PR-creation.
        self.assertFalse(secrets["DRIFT_SYNC_PAT"].get("required", False))

    def test_apply_step_sets_pythonpath(self) -> None:
        """Heredoc imports ``scripts.quality.bumps`` — PYTHONPATH must
        include ``platform/`` (where the platform repo is checked out)
        so the import resolves. Same fix shape as PR #136."""
        steps = self.doc["jobs"]["apply"]["steps"]
        apply_steps = [s for s in steps if s.get("id") == "apply"]
        self.assertEqual(len(apply_steps), 1)
        env = apply_steps[0].get("env", {}) or {}
        self.assertIn("PYTHONPATH", env)
        self.assertIn("platform", str(env["PYTHONPATH"]))

    def test_artifact_name_uses_safe_slug(self) -> None:
        """Bump-apply report artifact name uses slash-escaped slug
        (NTFS portability rule, learned in #130)."""
        steps = self.doc["jobs"]["apply"]["steps"]
        upload_steps = [
            s for s in steps
            if "uses" in s and "upload-artifact" in s["uses"]
        ]
        self.assertEqual(len(upload_steps), 1)
        self.assertIn(
            "steps.artifact_name.outputs.slug_safe",
            upload_steps[0]["with"]["name"],
        )

    def test_pr_step_gated_three_factors(self) -> None:
        """PR step requires (a) !dry_run, (b) DRIFT_SYNC_PAT present,
        (c) bumped_total > 0. Same shape as bump-shas-wave."""
        steps = self.doc["jobs"]["apply"]["steps"]
        pr_steps = [
            s for s in steps if "Commit + open bump PR" in s.get("name", "")
        ]
        self.assertEqual(len(pr_steps), 1)
        cond = pr_steps[0].get("if", "")
        self.assertIn("!inputs.dry_run", cond)
        self.assertIn("DRIFT_SYNC_PAT_PRESENT", cond)
        self.assertIn("steps.apply.outputs.bumped_total", cond)

    def test_env_var_indirection_inside_heredocs(self) -> None:
        """No ``${{ inputs.* }}`` / ``${{ secrets.* }}`` / ``${{ github.* }}``
        inside python heredocs — env-var indirection is the CWE-78 defence."""
        for body in re.findall(r"<<'PY'(.+?)PY", self.text, flags=re.DOTALL):
            with self.subTest(body_excerpt=body[:60]):
                self.assertNotIn("${{ inputs.", body)
                self.assertNotIn("${{ secrets.", body)
                self.assertNotIn("${{ github.", body)

    def test_top_level_permissions_empty(self) -> None:
        """Top-level perms empty; per-job perms locked down."""
        self.assertEqual(self.doc.get("permissions"), {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
