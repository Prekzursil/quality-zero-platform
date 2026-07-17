"""Tests for ``scripts.quality.verify_v2_deployment``."""

from __future__ import absolute_import

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.quality import verify_v2_deployment as vv

_REPO_ROOT = Path(__file__).resolve().parents[1]


class ShippedRepoAuditTests(unittest.TestCase):
    """Audit the real platform repo — Phase 1-4 artefacts must all be present."""

    def test_all_phase1_through_4_deliverables_present(self) -> None:
        """Every Phase 1-4 required path exists on the current checkout."""
        results = vv.audit_deployment(_REPO_ROOT)
        required_phases = ("phase1", "phase2", "phase3", "phase4")
        missing = [r for r in results if r.phase in required_phases and r.status == "missing"]
        self.assertEqual(missing, [], f"missing Phase 1-4 artefacts: {missing}")

    def test_summarise_buckets_results(self) -> None:
        """``summarise`` returns the JSON-safe counts + sorted path lists."""
        results = vv.audit_deployment(_REPO_ROOT)
        summary = vv.summarise(results)
        self.assertIn("ok_count", summary)
        self.assertIn("missing_count", summary)
        self.assertIn("warning_count", summary)
        total = summary["ok_count"] + summary["missing_count"] + summary["warning_count"]
        self.assertEqual(total, len(results))


class PhaseFiveOptionalTests(unittest.TestCase):
    """Phase 5 artefacts are tolerated as ``warning`` when absent."""

    def test_missing_phase5_file_marked_warning_not_missing(self) -> None:
        """A deleted Phase 5 path yields ``status=warning`` not ``missing``."""
        with tempfile.TemporaryDirectory() as tmp:
            # Empty temp repo = every Phase 5 optional is absent.
            results = vv.audit_deployment(Path(tmp))
        phase5_results = [r for r in results if r.phase == "phase5_optional"]
        for result in phase5_results:
            self.assertEqual(result.status, "warning")


class CliExitCodeTests(unittest.TestCase):
    """``main()`` honours the documented exit codes."""

    @staticmethod
    def _run(argv: list) -> int:
        """Invoke ``main()`` with patched ``sys.argv``."""
        with patch.object(sys, "argv", ["verify_v2_deployment.py", *argv]):
            return vv.main()

    @staticmethod
    def _run_capture(argv: list):
        """Invoke ``main()`` capturing ``(rc, stdout, stderr)``."""
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["verify_v2_deployment.py", *argv]), redirect_stdout(out), redirect_stderr(err):
            rc = vv.main()
        return rc, out.getvalue(), err.getvalue()

    def test_missing_repo_root_returns_2(self) -> None:
        """Exit 2 when ``--repo-root`` doesn't resolve to a directory."""
        rc = self._run(["--repo-root", "/does/not/exist"])
        self.assertEqual(rc, 2)

    def test_all_mode_fails_when_phase1_missing(self) -> None:
        """``--all`` exits 1 when any required artefact is absent."""
        with tempfile.TemporaryDirectory() as tmp:
            rc, _stdout, _stderr = self._run_capture(["--repo-root", tmp, "--all"])
        self.assertEqual(rc, 1)

    def test_missing_default_does_not_leak_workflow_commands_to_stdout(self) -> None:
        """Regression: ``--all`` without ``--emit-annotations`` keeps stdout clean.

        The Coverage 100 Gate ran red because this test module's
        ``main()`` call leaked ``::error::`` workflow commands into the
        coverage lane's stdout, which GitHub Actions then attached as
        check-run annotations. Diagnostics must stay on stderr and must
        not use the workflow-command prefix unless explicitly requested.
        """
        with tempfile.TemporaryDirectory() as tmp:
            rc, stdout, stderr = self._run_capture(["--repo-root", tmp, "--all"])
        self.assertEqual(rc, 1)
        self.assertNotIn("::error::", stdout)
        self.assertIn("missing deliverable:", stderr)
        self.assertNotIn("::error::", stderr)
        # stdout still carries the machine-readable summary only.
        self.assertIn('"missing_count"', stdout)

    def test_emit_annotations_writes_workflow_commands_to_stderr(self) -> None:
        """``--emit-annotations`` opts in to ``::error::`` commands on stderr."""
        with tempfile.TemporaryDirectory() as tmp:
            rc, stdout, stderr = self._run_capture(["--repo-root", tmp, "--all", "--emit-annotations"])
        self.assertEqual(rc, 1)
        self.assertIn("::error::missing deliverable:", stderr)
        self.assertNotIn("::error::", stdout)

    def test_all_mode_passes_on_complete_repo(self) -> None:
        """The current platform checkout should audit clean in ``--all`` mode."""
        rc = self._run(["--repo-root", str(_REPO_ROOT), "--all"])
        self.assertEqual(rc, 0)

    def test_default_mode_exits_0_even_with_missing(self) -> None:
        """Without ``--all``, the script reports but never fails."""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._run(["--repo-root", tmp])
        self.assertEqual(rc, 0)

    def test_out_json_writes_summary_to_file(self) -> None:
        """``--out-json`` writes the summary instead of printing to stdout."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "summary.json"
            rc = self._run(
                [
                    "--repo-root",
                    str(_REPO_ROOT),
                    "--out-json",
                    str(out),
                ]
            )
            self.assertEqual(rc, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("ok_count", payload)
            self.assertIn("missing", payload)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
