"""Contract tests for ``reusable-drift-sync.yml``.

Pins the Phase 3 §4 invariants that the per-repo drift-sync workflow
must hold. Most importantly: the artifact name escapes ``/`` since
``actions/upload-artifact@v4`` rejects forward slashes (NTFS rule)
and the wave was failing 15/15 on this exact issue before the
artifact-name step landed.
"""

from __future__ import absolute_import

import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]


_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "reusable-drift-sync.yml"
)


class ReusableDriftSyncWorkflowTests(unittest.TestCase):
    """Invariants on the per-repo drift-sync workflow."""

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the YAML once per test class."""
        cls.text = _WORKFLOW.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)

    def test_workflow_is_workflow_call(self) -> None:
        """Drift-sync is invoked from a caller workflow per-repo."""
        # ``on:`` parses as YAML boolean True under PyYAML.
        self.assertIn("workflow_call", self.doc[True])

    def test_required_inputs_are_declared(self) -> None:
        """``repo_slug`` is the only mandatory input (others have defaults)."""
        inputs = self.doc[True]["workflow_call"]["inputs"]
        self.assertIn("repo_slug", inputs)
        self.assertTrue(inputs["repo_slug"].get("required"))

    def test_artifact_name_step_replaces_forward_slash(self) -> None:
        """``Compute artifact-safe slug`` step replaces ``/`` so ``upload-
        artifact@v4`` doesn't reject names like ``Prekzursil/event-link``.
        Without this escape the entire wave failed 15/15 — see the
        first run of ``drift-sync-wave.yml`` (24963891110)."""
        steps = self.doc["jobs"]["drift-sync"]["steps"]
        compute_steps = [
            s for s in steps
            if s.get("id") == "artifact_name"
        ]
        self.assertEqual(
            len(compute_steps), 1,
            "artifact_name step missing — upload-artifact will reject "
            "names containing forward slashes",
        )
        run_body = compute_steps[0].get("run", "")
        # Use a substitution that produces a slash-free identifier.
        self.assertRegex(run_body, r"\$\{slug//\\?/")

    def test_upload_step_uses_artifact_safe_slug(self) -> None:
        """upload-artifact ``name:`` references the computed safe slug."""
        steps = self.doc["jobs"]["drift-sync"]["steps"]
        upload_steps = [
            s for s in steps
            if "uses" in s and "upload-artifact" in s["uses"]
        ]
        self.assertEqual(len(upload_steps), 1)
        name = upload_steps[0]["with"]["name"]
        self.assertIn("steps.artifact_name.outputs.slug_safe", name)
        self.assertNotIn("inputs.repo_slug", name)

    def test_consumer_repo_checkout_uses_drift_sync_pat_or_default(self) -> None:
        """Consumer-repo checkout falls back to ``github.token`` when no PAT."""
        steps = self.doc["jobs"]["drift-sync"]["steps"]
        consumer_checkouts = [
            s for s in steps
            if "uses" in s and s["uses"].startswith("actions/checkout")
            and "inputs.repo_slug" in str(s.get("with", {}).get("repository", ""))
        ]
        self.assertEqual(len(consumer_checkouts), 1)
        token_expr = consumer_checkouts[0]["with"]["token"]
        self.assertIn("DRIFT_SYNC_PAT", token_expr)
        self.assertIn("github.token", token_expr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
