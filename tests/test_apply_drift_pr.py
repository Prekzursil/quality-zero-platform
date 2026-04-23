"""Unit coverage for ``scripts.quality.apply_drift_pr``.

The workflow step invokes this CLI *after* ``drift_sync.py`` has
emitted its report. The tests stub ``subprocess.run`` so git + gh are
never actually called, and assert on the argument lists the script
constructs.
"""

from __future__ import absolute_import

import argparse
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import List
from unittest.mock import patch

from scripts.quality import apply_drift_pr as apr


class _FakeRunner:
    """Collect every ``(cmd, cwd)`` pair routed through the stub."""

    def __init__(self) -> None:
        """Initialise an empty call log."""
        self.calls: List[List[str]] = []

    def __call__(
        self, cmd, cwd=None, check=False, capture_output=False, text=False
    ) -> subprocess.CompletedProcess:
        """Record the invocation and return a success result."""
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=0)


def _write_report(payload: dict) -> Path:
    """Write ``payload`` to a temp file and return its path."""
    fd, name = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    path = Path(name)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class CollectOutOfSyncTests(unittest.TestCase):
    """``_collect_out_of_sync`` filters to missing + drift entries."""

    def test_only_missing_and_drift_retained(self) -> None:
        """in_sync entries are excluded."""
        entries = [
            {"status": "in_sync", "output_path": "x"},
            {"status": "drift", "output_path": "y"},
            {"status": "missing", "output_path": "z"},
        ]
        out = apr._collect_out_of_sync(entries)
        self.assertEqual([e["output_path"] for e in out], ["y", "z"])


class ApplyEntriesTests(unittest.TestCase):
    """``_apply_entries`` writes proposed content to disk under ``cwd``."""

    def test_writes_files_and_returns_staged_paths(self) -> None:
        """New files land in nested directories; return value lists them."""
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            entries = [
                {
                    "status": "missing",
                    "output_path": ".github/workflows/ci.yml",
                    "proposed_content": "name: CI\n",
                },
                {
                    "status": "drift",
                    "output_path": "codecov.yml",
                    "proposed_content": "codecov:\n  require_ci_to_pass: true\n",
                },
            ]
            applied = apr._apply_entries(entries, cwd)
        self.assertEqual(
            sorted(applied),
            sorted([".github/workflows/ci.yml", "codecov.yml"]),
        )

    def test_empty_output_path_is_skipped(self) -> None:
        """Entries without an output_path don't create empty files."""
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            applied = apr._apply_entries(
                [{"status": "drift", "output_path": "", "proposed_content": "x"}],
                cwd,
            )
        self.assertEqual(applied, [])


class BuildBodyTests(unittest.TestCase):
    """``_build_body`` composes a human-readable summary."""

    def test_summary_lists_each_out_of_sync_entry(self) -> None:
        """Each entry appears as a bullet with its status."""
        body = apr._build_body(
            [
                {"status": "drift", "output_path": "ci.yml"},
                {"status": "missing", "output_path": "codecov.yml"},
            ],
            "main",
        )
        self.assertIn("- `ci.yml` (drift)", body)
        self.assertIn("- `codecov.yml` (missing)", body)
        self.assertIn("Platform ref: `main`", body)


class RunDriftPrTests(unittest.TestCase):
    """``_run_drift_pr`` drives the apply + git + gh flow."""

    def _run(self, report: dict, **cli_kwargs) -> tuple:
        """Run the full flow; return ``(exit_code, call_log)``."""
        path = _write_report(report)
        self.addCleanup(path.unlink, missing_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                report=str(path),
                repo_slug=cli_kwargs.get("repo_slug", "Prekzursil/test-repo"),
                default_branch=cli_kwargs.get("default_branch", "main"),
                platform_ref=cli_kwargs.get("platform_ref", "main"),
                cwd=cli_kwargs.get("cwd", tmp),
                runner=None,
            )
            runner = _FakeRunner()
            rc = apr._run_drift_pr(args, runner)
        return rc, runner.calls

    def test_missing_report_returns_2(self) -> None:
        """Exit 2 when the report file doesn't exist."""
        args = argparse.Namespace(
            report="/nope.json", repo_slug="x/y", default_branch="main",
            platform_ref="main", cwd=".", runner=None,
        )
        rc = apr._run_drift_pr(args, _FakeRunner())
        self.assertEqual(rc, 2)

    def test_fully_in_sync_returns_0_without_git_calls(self) -> None:
        """All in_sync → exit 0 without invoking git/gh."""
        rc, calls = self._run(
            {"entries": [{"status": "in_sync", "output_path": "x"}]}
        )
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])

    def test_out_of_sync_runs_full_git_and_gh_sequence(self) -> None:
        """Drift triggers checkout+add+commit+push+gh pr create."""
        rc, calls = self._run(
            {
                "entries": [
                    {
                        "status": "drift",
                        "output_path": "codecov.yml",
                        "proposed_content": "codecov:\n  require_ci_to_pass: true\n",
                    }
                ]
            }
        )
        self.assertEqual(rc, 0)
        cmds = [call[:2] for call in calls]
        self.assertIn(["git", "checkout"], cmds)
        self.assertIn(["git", "add"], cmds)
        self.assertIn(["gh", "pr"], [call[:2] for call in calls])
        # Commit message asserts the drift-sync convention.
        commit_cmd = next(c for c in calls if "commit" in c)
        self.assertIn(
            "chore(drift-sync): apply template updates", commit_cmd,
        )

    def test_out_of_sync_but_no_writable_entries_returns_0(self) -> None:
        """If every entry has an empty output_path the script exits early."""
        rc, calls = self._run(
            {
                "entries": [
                    {
                        "status": "drift",
                        "output_path": "",
                        "proposed_content": "nop",
                    }
                ]
            }
        )
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])


class MainEntrypointTests(unittest.TestCase):
    """``main`` wires argparse + subprocess.run."""

    def test_main_parses_args_and_delegates(self) -> None:
        """With a minimal argv + report, main() returns the delegate's rc."""
        path = _write_report({"entries": [{"status": "in_sync", "output_path": "x"}]})
        self.addCleanup(path.unlink, missing_ok=True)
        with patch.object(
            apr, "_run_drift_pr", return_value=42
        ) as run_mock:
            with patch(
                "sys.argv",
                [
                    "apply_drift_pr.py",
                    "--report", str(path),
                    "--repo-slug", "x/y",
                ],
            ):
                rc = apr.main()
        self.assertEqual(rc, 42)
        run_mock.assert_called_once()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
