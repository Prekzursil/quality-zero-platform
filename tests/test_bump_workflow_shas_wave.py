"""Contract tests for the SHA-bump wave workflows.

Pins the per-repo and fleet-wide invariants for
``reusable-bump-workflow-shas.yml`` + ``bump-workflow-shas-wave.yml``.
"""

from __future__ import absolute_import

import re
import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]


_BASE = Path(__file__).resolve().parents[1] / ".github" / "workflows"
_PER_REPO = _BASE / "reusable-bump-workflow-shas.yml"
_WAVE = _BASE / "bump-workflow-shas-wave.yml"


class ReusableBumpWorkflowShasContract(unittest.TestCase):
    """Per-repo bump workflow invariants."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = _PER_REPO.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)

    def test_workflow_call_only(self) -> None:
        """Per-repo workflow is reusable, never directly dispatched."""
        on = self.doc[True]
        self.assertIn("workflow_call", on)
        self.assertNotIn("workflow_dispatch", on)
        self.assertNotIn("schedule", on)

    def test_required_inputs(self) -> None:
        """``repo_slug`` + ``target_sha`` are mandatory."""
        inputs = self.doc[True]["workflow_call"]["inputs"]
        for required in ("repo_slug", "target_sha"):
            with self.subTest(input=required):
                self.assertIn(required, inputs)
                self.assertTrue(inputs[required].get("required"))

    def test_dry_run_default_true(self) -> None:
        """Safety net: dry-run unless operator opts out."""
        inputs = self.doc[True]["workflow_call"]["inputs"]
        self.assertTrue(inputs["dry_run"].get("default", False))

    def test_artifact_name_uses_safe_slug(self) -> None:
        """Bump-report artifact name uses the slash-escaped slug."""
        steps = self.doc["jobs"]["bump"]["steps"]
        upload_steps = [
            s for s in steps
            if "uses" in s and "upload-artifact" in s["uses"]
        ]
        self.assertEqual(len(upload_steps), 1)
        name = upload_steps[0]["with"]["name"]
        self.assertIn("steps.artifact_name.outputs.slug_safe", name)

    def test_open_pr_step_gated_on_dry_run_and_pat_and_bumps(self) -> None:
        """PR step requires (a) opt-out of dry-run, (b) PAT present, (c) >0 bumps."""
        steps = self.doc["jobs"]["bump"]["steps"]
        pr_steps = [
            s for s in steps if "Open bump PR" in s.get("name", "")
        ]
        self.assertEqual(len(pr_steps), 1)
        cond = pr_steps[0].get("if", "")
        self.assertIn("!inputs.dry_run", cond)
        self.assertIn("DRIFT_SYNC_PAT_PRESENT", cond)
        self.assertIn("steps.bump.outputs.bumped_total", cond)

    def test_env_var_indirection_inside_heredocs(self) -> None:
        """No ``${{ inputs.* }}`` / ``${{ secrets.* }}`` inside python heredoc."""
        for body in re.findall(r"<<'PY'(.+?)PY", self.text, flags=re.DOTALL):
            with self.subTest(body_excerpt=body[:60]):
                self.assertNotIn("${{ inputs.", body)
                self.assertNotIn("${{ secrets.", body)
                self.assertNotIn("${{ github.", body)


class WaveDispatcherContract(unittest.TestCase):
    """Fleet-wide bump dispatcher invariants."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = _WAVE.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)

    def test_workflow_dispatch_only_no_cron(self) -> None:
        """Manual operator initiation only — no recurring fleet sweep."""
        on = self.doc[True]
        self.assertIn("workflow_dispatch", on)
        self.assertNotIn("schedule", on)

    def test_target_sha_required_dry_run_default_true(self) -> None:
        """Operator MUST supply target_sha; dry_run safety-net default."""
        inputs = self.doc[True]["workflow_dispatch"]["inputs"]
        self.assertTrue(inputs["target_sha"].get("required"))
        self.assertTrue(inputs["dry_run"].get("default", False))

    def test_concurrency_is_fixed_string(self) -> None:
        """Single-flight wave."""
        self.assertEqual(self.doc["concurrency"]["group"], "bump-workflow-shas-wave")

    def test_resolve_excludes_platform_self(self) -> None:
        """Enumeration step skips the platform repo itself (source of reusables)."""
        # The exclusion is in the python heredoc; check the source text.
        self.assertIn(
            "Prekzursil/quality-zero-platform",
            self.text,
        )
        self.assertRegex(
            self.text,
            r"if\s+slug\s*==\s*\"Prekzursil/quality-zero-platform\"",
        )

    def test_bump_job_calls_per_repo_reusable(self) -> None:
        """Wave's matrix job invokes the per-repo reusable workflow."""
        bump_job = self.doc["jobs"]["bump"]
        self.assertIn("reusable-bump-workflow-shas.yml", bump_job["uses"])
        self.assertIs(bump_job["strategy"].get("fail-fast"), False)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
