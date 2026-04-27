"""Contract tests for ``scheduled-alerts.yml``.

Pins the Phase 5 §8 invariants for the cron-driven alert dispatcher:

* Runs on schedule AND supports manual dispatch.
* Concurrency key prevents overlapping cron runs.
* Defaults to dry-run on manual dispatch (safety net).
* Env-var indirection for every ``${{ ... }}`` inside the python
  heredoc (blocks Semgrep CWE-78).
* Permissions are narrow (contents:read + issues:write only).
* Checkout does not persist credentials (CodeQL safety).
"""

from __future__ import absolute_import

import re
import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]

_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "scheduled-alerts.yml"
)


class ScheduledAlertsWorkflowTests(unittest.TestCase):
    """Invariants on the scheduled-alerts workflow."""

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the YAML once per test class."""
        cls.text = _WORKFLOW.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)

    def test_workflow_has_schedule_and_dispatch(self) -> None:
        """Runs on cron AND on manual dispatch."""
        # ``on:`` parses as YAML boolean True — access via that key.
        on_block = self.doc[True]
        self.assertIn("schedule", on_block)
        self.assertIn("workflow_dispatch", on_block)

    def test_schedule_is_every_six_hours(self) -> None:
        """Cron expression runs at least every 6 hours."""
        schedule = self.doc[True]["schedule"]
        self.assertTrue(schedule, "no schedule entries defined")
        cron = str(schedule[0].get("cron", ""))
        self.assertIn("*/6", cron)

    def test_dry_run_default_is_true(self) -> None:
        """Manual dispatch defaults to dry-run so operators never
        accidentally open a flood of issues."""
        inputs = self.doc[True]["workflow_dispatch"].get("inputs", {}) or {}
        self.assertIn("dry_run", inputs)
        self.assertTrue(inputs["dry_run"].get("default", False))

    def test_concurrency_key_is_fixed_string(self) -> None:
        """Concurrency group is a fixed string so overlapping crons merge."""
        concurrency = self.doc.get("concurrency", {})
        self.assertEqual(concurrency.get("group"), "scheduled-alerts")

    def test_env_var_indirection_inside_heredoc(self) -> None:
        """No ``${{ inputs.* }}`` / ``${{ github.* }}`` inside python body."""
        heredocs = re.findall(r"<<'PY'(.+?)PY", self.text, flags=re.DOTALL)
        self.assertTrue(heredocs, "expected at least one python heredoc")
        for body in heredocs:
            with self.subTest(body_excerpt=body[:60]):
                self.assertNotIn("${{ inputs.", body)
                self.assertNotIn("${{ github.", body)
                self.assertNotIn("${{ secrets.", body)

    def test_permissions_are_narrow(self) -> None:
        """Top-level perms empty; dispatch job has contents:read + issues:write."""
        self.assertEqual(self.doc.get("permissions"), {})
        perms = self.doc["jobs"]["dispatch"].get("permissions", {})
        self.assertEqual(perms.get("contents"), "read")
        self.assertEqual(perms.get("issues"), "write")

    def test_checkout_does_not_persist_credentials(self) -> None:
        """``persist-credentials: false`` — CodeQL safety."""
        steps = self.doc["jobs"]["dispatch"]["steps"]
        checkout_steps = [
            s for s in steps
            if "uses" in s and s["uses"].startswith("actions/checkout")
        ]
        self.assertEqual(len(checkout_steps), 1)
        self.assertFalse(
            checkout_steps[0].get("with", {}).get("persist-credentials", True),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
