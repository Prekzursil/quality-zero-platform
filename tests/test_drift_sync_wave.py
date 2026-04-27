"""Contract tests for ``drift-sync-wave.yml`` fleet-wide dispatcher."""

from __future__ import absolute_import

import re
import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]

_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "drift-sync-wave.yml"
)


class DriftSyncWaveWorkflowTests(unittest.TestCase):
    """Invariants on the fleet-wide drift-sync wave dispatcher."""

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the YAML once per test class."""
        cls.text = _WORKFLOW.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)

    def test_workflow_dispatch_only(self) -> None:
        """Only ``workflow_dispatch`` — no cron to avoid mass-fleet drift."""
        on_block = self.doc[True]
        self.assertIn("workflow_dispatch", on_block)
        self.assertNotIn("schedule", on_block)

    def test_dry_run_defaults_to_true(self) -> None:
        """Safety net: operators must explicitly opt out of dry-run."""
        inputs = self.doc[True]["workflow_dispatch"]["inputs"]
        self.assertIn("dry_run", inputs)
        self.assertTrue(inputs["dry_run"].get("default", False))

    def test_concurrency_is_fixed_string(self) -> None:
        """Single-flight wave — two overlapping dispatches merge."""
        concurrency = self.doc.get("concurrency", {})
        self.assertEqual(concurrency.get("group"), "drift-sync-wave")

    def test_env_var_indirection_inside_heredoc(self) -> None:
        """No ``${{ inputs.* }}`` inside the python body."""
        heredocs = re.findall(r"<<'PY'(.+?)PY", self.text, flags=re.DOTALL)
        self.assertTrue(heredocs)
        for body in heredocs:
            with self.subTest(body_excerpt=body[:60]):
                self.assertNotIn("${{ inputs.", body)
                self.assertNotIn("${{ github.", body)
                self.assertNotIn("${{ secrets.", body)

    def test_sync_job_uses_reusable_workflow(self) -> None:
        """The per-repo job invokes ``reusable-drift-sync.yml``."""
        jobs = self.doc["jobs"]
        self.assertIn("sync", jobs)
        uses = jobs["sync"].get("uses", "")
        self.assertIn("reusable-drift-sync.yml", uses)

    def test_matrix_strategy_fanouts_per_slug(self) -> None:
        """Matrix strategy expands to one job per fleet slug."""
        sync_job = self.doc["jobs"]["sync"]
        strategy = sync_job.get("strategy", {})
        self.assertIn("matrix", strategy)
        self.assertIn("slug", strategy["matrix"])
        # fail-fast MUST be false so one broken repo doesn't abort the wave.
        self.assertIs(strategy.get("fail-fast"), False)

    def test_drift_sync_pat_forwarded_as_secret(self) -> None:
        """The wave passes DRIFT_SYNC_PAT into the reusable workflow."""
        sync_secrets = self.doc["jobs"]["sync"].get("secrets", {})
        self.assertIn("DRIFT_SYNC_PAT", sync_secrets)

    def test_resolve_job_has_read_only_perms(self) -> None:
        """Enumeration job only needs contents:read."""
        perms = self.doc["jobs"]["resolve-fleet"].get("permissions", {})
        self.assertEqual(perms.get("contents"), "read")

    def test_top_level_permissions_empty(self) -> None:
        """Top-level permissions block is empty — job-level lockdown."""
        self.assertEqual(self.doc.get("permissions"), {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
