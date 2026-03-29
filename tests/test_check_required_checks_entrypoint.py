from __future__ import absolute_import

import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List, Mapping, Tuple, cast
from unittest.mock import MagicMock, patch

from scripts.quality import check_required_checks as checks_module


class RequiredChecksEntrypointTests(unittest.TestCase):
    """Cover CLI entrypoint behavior and report-writing outcomes."""

    @staticmethod
    def _run_main_with_payload(
        *,
        payload: Mapping[str, object],
        write_report_result: int = 0,
    ) -> Tuple[int, MagicMock]:
        """Execute the gate entrypoint with a mocked payload and writer."""
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            sys,
            "argv",
            [
                "check_required_checks.py",
                "--repo",
                "Prekzursil/quality-zero-platform",
                "--sha",
                "abc123",
                "--required-context",
                "Coverage 100 Gate",
                "--out-json",
                str(Path(tmpdir) / "required-checks.json"),
                "--out-md",
                str(Path(tmpdir) / "required-checks.md"),
            ],
        ), patch.dict(
            os.environ,
            {"GH_TOKEN": "token-123"},
            clear=False,
        ), patch.object(
            checks_module,
            "_wait_for_payload",
            return_value=payload,
        ), patch.object(
            checks_module,
            "write_report",
            return_value=write_report_result,
        ) as writer:
            result = checks_module.main()
        return result, cast(MagicMock, writer)

    def _assert_entrypoint_requires_contexts(
        self,
        *,
        repo_root: Path,
        sys_path: List[str],
    ) -> None:
        """Assert the CLI exits when no required contexts are configured."""
        with patch.object(
            sys,
            "argv",
            [
                "check_required_checks.py",
                "--repo",
                "Prekzursil/quality-zero-platform",
                "--sha",
                "abc123",
            ],
        ), patch.object(
            sys,
            "path",
            sys_path,
        ), patch.dict(
            os.environ,
            {"GH_TOKEN": "token-123"},
            clear=False,
        ), self.assertRaisesRegex(
            SystemExit,
            "At least one --required-context is required",
        ):
            runpy.run_path(
                str(repo_root / "scripts" / "quality" / "check_required_checks.py"),
                run_name="__main__",
            )

    def test_main_rejects_missing_required_contexts(self) -> None:
        """Require at least one check context before the CLI can run."""
        with patch.object(
            sys,
            "argv",
            [
                "check_required_checks.py",
                "--repo",
                "Prekzursil/quality-zero-platform",
                "--sha",
                "abc123",
            ],
        ), patch.dict(
            os.environ,
            {"GH_TOKEN": "token-123"},
            clear=False,
        ), self.assertRaisesRegex(
            SystemExit,
            "At least one --required-context is required",
        ):
            checks_module.main()

    def test_main_rejects_missing_github_token(self) -> None:
        """Exit when neither GitHub token environment variable is populated."""
        with patch.object(
            sys,
            "argv",
            [
                "check_required_checks.py",
                "--repo",
                "Prekzursil/quality-zero-platform",
                "--sha",
                "abc123",
                "--required-context",
                "Coverage 100 Gate",
            ],
        ), patch.dict(
            os.environ,
            {"GH_TOKEN": "", "GITHUB_TOKEN": ""},
            clear=False,
        ), self.assertRaisesRegex(
            SystemExit,
            "GITHUB_TOKEN or GH_TOKEN is required",
        ):
            checks_module.main()

    def test_script_entrypoint_raises_system_exit_from_main(self) -> None:
        """Propagate the CLI validation error through the script entrypoint."""
        repo_root = Path(__file__).resolve().parents[1]
        without_empty = [entry for entry in sys.path if entry != str(repo_root) and entry != ""]

        for sys_path in (without_empty, ["", *without_empty]):
            if "" not in sys_path:
                sys_path.insert(0, "")
            self._assert_entrypoint_requires_contexts(
                repo_root=repo_root,
                sys_path=sys_path,
            )

    def test_script_entrypoint_uses_existing_empty_sys_path_entry(self) -> None:
        """Reuse an existing empty sys.path entry when bootstrapping the script."""
        repo_root = Path(__file__).resolve().parents[1]
        sys_path = [
            "",
            *(entry for entry in sys.path if entry != str(repo_root) and entry != ""),
        ]

        self._assert_entrypoint_requires_contexts(
            repo_root=repo_root,
            sys_path=sys_path,
        )

    def test_main_returns_success_when_report_written_and_payload_passes(self) -> None:
        """Return zero when the payload passes and report writing succeeds."""
        payload = {
            "status": "pass",
            "repo": "Prekzursil/quality-zero-platform",
            "sha": "abc123",
            "timestamp_utc": "2026-03-15T00:00:00+00:00",
            "missing": [],
            "failed": [],
        }
        result, writer = self._run_main_with_payload(payload=payload)

        self.assertEqual(result, 0)
        writer.assert_called_once()

    def test_main_returns_failure_when_payload_is_not_green(self) -> None:
        """Return one when the collected payload still contains failures."""
        payload = {
            "status": "fail",
            "repo": "Prekzursil/quality-zero-platform",
            "sha": "abc123",
            "timestamp_utc": "2026-03-15T00:00:00+00:00",
            "missing": ["Coverage 100 Gate"],
            "failed": [],
        }
        result, _writer = self._run_main_with_payload(payload=payload)

        self.assertEqual(result, 1)

    def test_main_returns_write_report_exit_code_when_report_write_fails(self) -> None:
        """Bubble up a non-zero report writer exit code to the CLI caller."""
        payload = {
            "status": "pass",
            "repo": "Prekzursil/quality-zero-platform",
            "sha": "abc123",
            "timestamp_utc": "2026-03-15T00:00:00+00:00",
            "missing": [],
            "failed": [],
        }
        result, _writer = self._run_main_with_payload(
            payload=payload,
            write_report_result=7,
        )

        self.assertEqual(result, 7)
