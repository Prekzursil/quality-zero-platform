"""Test run coverage gate."""

from __future__ import absolute_import, division

import io
import json
import runpy
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import List
from unittest.mock import patch

from scripts.quality.common import DEFAULT_COVERAGE_JSON, DEFAULT_COVERAGE_MD

from scripts.quality import run_coverage_gate


class RunCoverageGateTests(unittest.TestCase):
    """Run Coverage Gate Tests."""

    @staticmethod
    def _assert_run_shell_invocation(
        *,
        shell_name: str,
        resolved_path: str,
        expected_argv: List[str],
    ) -> None:
        """Handle assert run shell invocation."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            with (
                patch(
                    "scripts.quality.run_coverage_gate._path_exists",
                    side_effect=lambda raw_path: raw_path == resolved_path,
                ),
                patch(
                    "scripts.quality.run_coverage_gate.subprocess.run",
                    return_value=object(),
                ) as mock_run,
            ):
                run_coverage_gate._run_shell(
                    "echo coverage", shell_name=shell_name, cwd=cwd
                )

        mock_run.assert_called_once_with(
            expected_argv,
            cwd=cwd,
            input="echo coverage",
            text=True,
            shell=False,
            check=True,
        )

    @staticmethod
    def _coverage_assert_fixture():
        """Handle coverage assert fixture."""
        temp_dir = tempfile.TemporaryDirectory()
        repo_dir = Path(temp_dir.name) / "repo"
        repo_dir.mkdir()
        platform_dir = Path(temp_dir.name) / "platform"
        platform_dir.mkdir()
        coverage_dir = repo_dir / "coverage"
        coverage_dir.mkdir()
        (coverage_dir / "platform-coverage.xml").write_text(
            (
                "<coverage lines-valid=\"1\" lines-covered=\"1\"><packages>"
                "<package><classes><class filename=\"src/app.py\"><lines>"
                '<line number="1" hits="1" />'
                "</lines></class></classes></package></packages></coverage>"
            ),
            encoding="utf-8",
        )
        coverage = {
            "inputs": [
                {
                    "format": "xml",
                    "name": "platform",
                    "path": str(coverage_dir / "platform-coverage.xml"),
                },
            ],
            "require_sources": ["src/app.py"],
            "min_percent": 100.0,
            "branch_min_percent": 85.0,
        }
        return temp_dir, repo_dir, platform_dir, coverage_dir, coverage

    def test_coverage_mode_prefers_event_specific_override(self) -> None:
        """Cover coverage mode prefers event specific override."""
        self.assertEqual(
            run_coverage_gate._coverage_mode(
                {
                    "assert_mode": {
                        "default": "enforce",
                        "pull_request": "evidence_only",
                    }
                },
                "pull_request",
            ),
            "evidence_only",
        )

    @staticmethod
    def test_run_shell_ignores_blank_commands() -> None:
        """Cover run shell ignores blank commands."""
        with patch("scripts.quality.run_coverage_gate.subprocess.run") as mock_run:
            run_coverage_gate._run_shell("   ", shell_name="bash", cwd=Path.cwd())

        mock_run.assert_not_called()

    def test_run_shell_passes_repo_command_via_stdin_to_static_shell_argv(self) -> None:
        """Cover run shell passes repo command via stdin to static shell argv."""
        self._assert_run_shell_invocation(
            shell_name="pwsh",
            resolved_path="/usr/bin/pwsh",
            expected_argv=["/usr/bin/pwsh", "-NoLogo", "-Command", "-"],
        )

    def test_run_shell_prefers_windows_powershell_path_when_available(self) -> None:
        """Cover run shell prefers windows powershell path when available."""
        self._assert_run_shell_invocation(
            shell_name="pwsh",
            resolved_path=r"C:\Program Files\PowerShell\7\pwsh.exe",
            expected_argv=[
                r"C:\Program Files\PowerShell\7\pwsh.exe",
                "-NoLogo",
                "-Command",
                "-",
            ],
        )

    def test_run_shell_supports_bash_with_static_shell_argv(self) -> None:
        """Cover run shell supports bash with static shell argv."""
        self._assert_run_shell_invocation(
            shell_name="bash",
            resolved_path="/usr/bin/bash",
            expected_argv=["/usr/bin/bash", "-s"],
        )

    def test_run_shell_supports_bin_bash_fallback(self) -> None:
        """Cover run shell supports bin bash fallback."""
        self._assert_run_shell_invocation(
            shell_name="bash",
            resolved_path="/bin/bash",
            expected_argv=["/bin/bash", "-s"],
        )

    def test_run_shell_requires_resolved_shell_executable(self) -> None:
        """Cover run shell requires resolved shell executable."""
        with (
            patch("scripts.quality.run_coverage_gate._path_exists", return_value=False),
            self.assertRaisesRegex(
                FileNotFoundError, "Unable to locate required shell executable: bash"
            ),
        ):
            run_coverage_gate._run_shell(
                "echo coverage", shell_name="bash", cwd=Path.cwd()
            )

    def test_run_shell_requires_resolved_powershell_executable(self) -> None:
        """Cover run shell requires resolved powershell executable."""
        with (
            patch("scripts.quality.run_coverage_gate._path_exists", return_value=False),
            self.assertRaisesRegex(
                FileNotFoundError, "Unable to locate required shell executable: pwsh"
            ),
        ):
            run_coverage_gate._run_shell(
                "echo coverage", shell_name="pwsh", cwd=Path.cwd()
            )

    def test_path_exists_reports_real_files(self) -> None:
        """Cover path exists reports real files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "exists.txt"
            target.write_text("ok", encoding="utf-8")

            self.assertTrue(run_coverage_gate._path_exists(str(target)))
            self.assertFalse(
                run_coverage_gate._path_exists(str(target.with_name("missing.txt")))
            )

    def test_run_assert_coverage_invokes_module_in_repo_cwd(self) -> None:
        """Cover run assert coverage invokes module in repo cwd."""
        temp_dir, repo_dir, platform_dir, coverage_dir, coverage = (
            self._coverage_assert_fixture()
        )
        with temp_dir:
            observed_cwd = None
            observed_argv = None

            def fake_main() -> int:
                """Handle fake main."""
                nonlocal observed_cwd, observed_argv
                observed_cwd = Path.cwd()
                observed_argv = list(sys.argv)
                return 17

            with patch(
                "scripts.quality.run_coverage_gate.assert_coverage_100.main",
                side_effect=fake_main,
            ):
                result = run_coverage_gate._run_assert_coverage_100(
                    coverage,
                    repo_dir=repo_dir,
                    platform_dir=platform_dir,
                )

        self.assertEqual(result, 17)
        self.assertEqual(observed_cwd, repo_dir)
        self.assertEqual(
            observed_argv,
            [
                str(platform_dir / "scripts" / "quality" / "assert_coverage_100.py"),
                "--xml",
                f"platform={coverage_dir / 'platform-coverage.xml'}",
                "--require-source",
                "src/app.py",
                "--min-percent",
                "100.0",
                "--branch-min-percent",
                "85.0",
                "--out-json",
                DEFAULT_COVERAGE_JSON,
                "--out-md",
                DEFAULT_COVERAGE_MD,
            ],
        )

    def test_run_assert_coverage_omits_branch_threshold_when_disabled(self) -> None:
        """Cover run assert coverage omits branch threshold when disabled."""
        temp_dir, repo_dir, platform_dir, _coverage_dir, coverage = (
            self._coverage_assert_fixture()
        )
        coverage["branch_min_percent"] = None
        with temp_dir:
            observed_argv = None

            def fake_main() -> int:
                """Handle fake main."""
                nonlocal observed_argv
                observed_argv = list(sys.argv)
                return 0

            with patch(
                "scripts.quality.run_coverage_gate.assert_coverage_100.main",
                side_effect=fake_main,
            ):
                result = run_coverage_gate._run_assert_coverage_100(
                    coverage,
                    repo_dir=repo_dir,
                    platform_dir=platform_dir,
                )

        self.assertEqual(result, 0)
        self.assertIsNotNone(observed_argv)
        observed_argv = observed_argv or []
        self.assertNotIn("--branch-min-percent", observed_argv)

    def test_main_runs_profile_command_and_direct_assertion(self) -> None:
        """Cover main runs profile command and direct assertion."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            repo_dir = temp_path / "repo"
            repo_dir.mkdir()
            profile_json = temp_path / "profile.json"
            profile_json.write_text(
                json.dumps(
                    {
                        "coverage": {
                            "command": "echo coverage",
                            "shell": "bash",
                            "inputs": [],
                            "min_percent": 100.0,
                        }
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch("scripts.quality.run_coverage_gate._run_shell") as mock_shell,
                patch(
                    "scripts.quality.run_coverage_gate._run_assert_coverage_100",
                    return_value=23,
                ) as mock_assert,
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_coverage_gate.py",
                        "--profile-json",
                        str(profile_json),
                        "--event-name",
                        "push",
                        "--repo-dir",
                        str(repo_dir),
                    ],
                ),
            ):
                result = run_coverage_gate.main()

        self.assertEqual(result, 23)
        mock_shell.assert_called_once_with(
            "echo coverage", shell_name="bash", cwd=repo_dir.resolve()
        )
        mock_assert.assert_called_once()

    def test_evidence_only_mode_skips_assertion_and_returns_success(self) -> None:
        """Cover evidence only mode skips assertion and returns success."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            profile_json = temp_path / "profile.json"
            profile_json.write_text(
                json.dumps(
                    {
                        "coverage": {
                            "command": "",
                            "shell": "bash",
                            "assert_mode": {"default": "evidence_only"},
                            "evidence_note": "hard gate is enforced elsewhere",
                        }
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch("scripts.quality.run_coverage_gate._run_shell") as mock_shell,
                patch(
                    "scripts.quality.run_coverage_gate._run_assert_coverage_100"
                ) as mock_assert,
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_coverage_gate.py",
                        "--profile-json",
                        str(profile_json),
                        "--event-name",
                        "push",
                    ],
                ),
            ):
                result = run_coverage_gate.main()

        self.assertEqual(result, 0)
        mock_shell.assert_called_once_with(
            "", shell_name="bash", cwd=Path.cwd().resolve()
        )
        mock_assert.assert_not_called()

