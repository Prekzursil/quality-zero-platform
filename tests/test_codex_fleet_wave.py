"""Behavioural tests for ``.github/workflows/codex-fleet-wave.yml``.

Without ``CODEX_DISPATCH_PAT`` no runner lane is configured. The daily cron used
to fail on that every night (105 straight red runs), which only taught everyone
to ignore the workflow. A scheduled run now skips visibly; a manual dispatch,
where someone explicitly asked for a wave, still fails. The dispatch step is
EXTRACTED from the workflow and EXECUTED under a real bash.
"""

from __future__ import absolute_import

import tempfile
import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from tests.lean_gate_support import ROOT, find_bash, run_step_script

WORKFLOW = ROOT / ".github" / "workflows" / "codex-fleet-wave.yml"
STEP = "Dispatch runner (serial, deduped) + roll-up"


class MissingDispatchTokenTests(unittest.TestCase):
    """A missing PAT is a skip on the cron and a failure on a manual dispatch."""

    @classmethod
    def setUpClass(cls) -> None:
        """Extract the dispatch step once."""
        document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        steps = document["jobs"]["sweep"]["steps"]
        cls.step = next(step for step in steps if step.get("name") == STEP)

    def _run(self, event: str):
        bash = find_bash()
        if bash is None:  # pragma: no cover - environment guard
            self.skipTest("no real bash found")
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            summary = workdir / "summary.md"
            result = run_step_script(
                str(self.step["run"]),
                workdir=workdir,
                bash=bash,
                env_overrides={
                    "DISPATCH_TOKEN": "",
                    "EVENT_NAME": event,
                    "GITHUB_STEP_SUMMARY": str(summary),
                    "RUNNER_TEMP": str(workdir),
                },
            )
            written = summary.read_text(encoding="utf-8") if summary.exists() else ""
        return result, written

    def test_the_event_reaches_the_step_through_env(self) -> None:
        """The decision reads github.event_name via env:, never spliced into the script."""
        self.assertEqual(self.step["env"]["EVENT_NAME"], "${{ github.event_name }}")
        self.assertNotIn("${{", str(self.step["run"]))

    def test_a_scheduled_run_skips_visibly(self) -> None:
        """Green, with a notice and a summary saying why nothing was dispatched."""
        result, summary = self._run("schedule")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::notice title=Codex Fleet Wave skipped::CODEX_DISPATCH_PAT is not configured", result.stdout)
        self.assertIn("# Codex Fleet Wave -- skipped", summary)
        self.assertIn("`CODEX_DISPATCH_PAT` is not configured", summary)

    def test_a_manual_dispatch_still_fails_loudly(self) -> None:
        """Someone asked for a wave that cannot run."""
        result, summary = self._run("workflow_dispatch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::CODEX_DISPATCH_PAT is not set", result.stdout)
        self.assertEqual(summary, "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
