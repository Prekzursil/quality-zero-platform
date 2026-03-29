"""Test run qlty zero."""

from __future__ import absolute_import

import importlib
import json
import tempfile
import unittest
from pathlib import Path
import sys
from typing import Any, Sequence, Tuple
from unittest.mock import patch

import scripts.quality.run_qlty_zero as run_qlty_zero


class RunQltyZeroTests(unittest.TestCase):
    """Run Qlty Zero Tests."""

    @staticmethod
    def _run_main_with_completed_processes(
        *completed_processes: Any,
    ) -> Tuple[int, Path, str, str, Sequence[Any]]:
        """Handle run main with completed processes."""
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir) / "repo"
            repo_dir.mkdir()
            json_path = repo_dir / "qlty-zero" / "qlty-zero.json"
            md_path = repo_dir / "qlty-zero" / "qlty-zero.md"

            with (
                patch(
                    "scripts.quality.run_qlty_zero.subprocess.run",
                    side_effect=list(completed_processes),
                ) as mock_run,
                patch(
                    "scripts.quality.run_qlty_zero.shutil.which",
                    return_value=r"C:\Tools\qlty.exe",
                ),
                patch(
                    "scripts.quality.run_qlty_zero.sys.argv",
                    [
                        "run_qlty_zero.py",
                        "--repo-dir",
                        str(repo_dir),
                        "--out-json",
                        str(json_path),
                        "--out-md",
                        str(md_path),
                    ],
                ),
            ):
                result = run_qlty_zero.main()

            json_text = json_path.read_text(encoding="utf-8")
            markdown = md_path.read_text(encoding="utf-8")
            return result, repo_dir, json_text, markdown, mock_run.call_args_list

    def test_tail_lines_truncates_to_the_requested_tail_and_smells_detection_handles_empty_output(
        self,
    ) -> None:
        """Cover tail lines truncates to the requested tail and smells detection handles empty output."""
        self.assertEqual(
            run_qlty_zero._tail_lines("line-1\nline-2\nline-3", limit=2),
            "line-2\nline-3",
        )
        self.assertFalse(run_qlty_zero._smells_output_indicates_findings(""))
        self.assertFalse(run_qlty_zero._smells_output_indicates_findings("no issues"))
        self.assertFalse(
            run_qlty_zero._smells_output_indicates_findings(
                "\u001b[32m✔ No issues\u001b[0m"
            )
        )
        self.assertTrue(run_qlty_zero._smells_output_indicates_findings("one smell"))

    def test_render_md_uses_none_when_a_check_has_no_output_tail(self) -> None:
        """Cover render md uses none when a check has no output tail."""
        markdown = run_qlty_zero._render_md(
            {
                "status": "pass",
                "return_code": 0,
                "timestamp_utc": "2026-03-18T00:00:00+00:00",
                "checks": [
                    {
                        "name": "check",
                        "status": "pass",
                        "return_code": 0,
                        "command": ["qlty", "check"],
                        "output_tail": "",
                    }
                ],
            }
        )

        self.assertIn("- None", markdown)
        self.assertIn("## check", markdown)

    def test_build_qlty_check_argv_uses_fail_on_any_issue_semantics(self) -> None:
        """Cover build qlty check argv uses fail on any issue semantics."""
        self.assertEqual(
            run_qlty_zero._build_qlty_check_argv(),
            [
                "qlty",
                "check",
                "--all",
                "--fail-level",
                "note",
                "--summary",
            ],
        )

    def test_build_qlty_smells_argv_runs_repo_wide_structure_analysis(self) -> None:
        """Cover build qlty smells argv runs repo wide structure analysis."""
        self.assertEqual(
            run_qlty_zero._build_qlty_smells_argv(),
            [
                "qlty",
                "smells",
                "--all",
                "--quiet",
                "--no-snippets",
            ],
        )

    def test_main_runs_qlty_commands_with_static_argv_in_repo_root(self) -> None:
        """Cover main runs qlty commands with static argv in repo root."""
        completed_check = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "plugin summary\n", "stderr": ""},
        )()
        completed_smells = type(
            "Completed",
            (),
            {"returncode": 1, "stdout": "smell summary\n", "stderr": "stderr noise\n"},
        )()

        result, repo_dir, _json_text, _markdown, call_args = (
            self._run_main_with_completed_processes(
                completed_check,
                completed_smells,
            )
        )

        self.assertEqual(result, 1)
        self.assertEqual(len(call_args), 2)
        self.assertEqual(
            call_args[0].args[0],
            ["qlty", "check", "--all", "--fail-level", "note", "--summary"],
        )
        self.assertEqual(
            call_args[1].args[0],
            ["qlty", "smells", "--all", "--quiet", "--no-snippets"],
        )
        self.assertEqual(call_args[0].kwargs["executable"], r"C:\Tools\qlty.exe")
        self.assertEqual(call_args[1].kwargs["executable"], r"C:\Tools\qlty.exe")
        for call in call_args:
            self.assertEqual(Path(call.kwargs["cwd"]).resolve(), repo_dir.resolve())
            self.assertFalse(call.kwargs.get("shell", False))
            self.assertFalse(call.kwargs["check"])
            self.assertTrue(call.kwargs["capture_output"])
            self.assertTrue(call.kwargs["text"])

    def test_main_writes_failure_artifacts_when_smells_report_findings(self) -> None:
        """Cover main writes failure artifacts when smells report findings."""
        completed_check = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "plugin summary\n", "stderr": ""},
        )()
        completed_smells = type(
            "Completed",
            (),
            {"returncode": 1, "stdout": "smell summary\n", "stderr": "stderr noise\n"},
        )()

        result, _repo_dir, json_text, markdown, _call_args = (
            self._run_main_with_completed_processes(
                completed_check,
                completed_smells,
            )
        )

        self.assertEqual(result, 1)
        payload = json.loads(json_text)
        self.assertEqual(payload["status"], "fail")
        self.assertEqual(payload["return_code"], 1)
        self.assertEqual(
            payload["commands"],
            [
                ["qlty", "check", "--all", "--fail-level", "note", "--summary"],
                ["qlty", "smells", "--all", "--quiet", "--no-snippets"],
            ],
        )
        self.assertEqual(payload["checks"][0]["name"], "check")
        self.assertEqual(payload["checks"][1]["name"], "smells")
        self.assertIn("smell summary", payload["output_tail"])
        self.assertIn("stderr noise", payload["output_tail"])
        self.assertIn("QLTY Zero", markdown)
        self.assertIn("## check", markdown)
        self.assertIn("## smells", markdown)

    def test_main_builds_missing_command_payload_when_qlty_is_missing(self) -> None:
        """Cover main builds missing command payload when qlty is missing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir) / "repo"
            repo_dir.mkdir()
            json_path = repo_dir / "qlty-zero" / "qlty-zero.json"
            md_path = repo_dir / "qlty-zero" / "qlty-zero.md"

            with (
                patch("scripts.quality.run_qlty_zero.shutil.which", return_value=None),
                patch(
                    "scripts.quality.run_qlty_zero._write_payload", return_value=0
                ) as mock_write,
                patch(
                    "scripts.quality.run_qlty_zero.sys.argv",
                    [
                        "run_qlty_zero.py",
                        "--repo-dir",
                        str(repo_dir),
                        "--out-json",
                        str(json_path),
                        "--out-md",
                        str(md_path),
                    ],
                ),
            ):
                result = run_qlty_zero.main()

            self.assertEqual(result, 1)
            self.assertEqual(mock_write.call_count, 1)
            payload = mock_write.call_args.args[0]
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["return_code"], 1)
            self.assertEqual(
                [check["name"] for check in payload["checks"]], ["check", "smells"]
            )
            self.assertTrue(
                all(
                    "command not found" in check["output_tail"]
                    for check in payload["checks"]
                )
            )

    def test_main_returns_the_report_error_when_report_writing_fails(self) -> None:
        """Cover main returns the report error when report writing fails."""
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir) / "repo"
            repo_dir.mkdir()

            with (
                patch(
                    "scripts.quality.run_qlty_zero._run_checks",
                    return_value=(
                        [
                            {
                                "name": "check",
                                "status": "pass",
                                "return_code": 0,
                                "command": ["qlty", "check"],
                                "output_tail": "clean",
                            }
                        ],
                        0,
                    ),
                ),
                patch("scripts.quality.run_qlty_zero._write_payload", return_value=7),
                patch(
                    "scripts.quality.run_qlty_zero.sys.argv",
                    [
                        "run_qlty_zero.py",
                        "--repo-dir",
                        str(repo_dir),
                        "--out-json",
                        str(repo_dir / "qlty-zero" / "qlty-zero.json"),
                        "--out-md",
                        str(repo_dir / "qlty-zero" / "qlty-zero.md"),
                    ],
                ),
            ):
                result = run_qlty_zero.main()

            self.assertEqual(result, 7)

    def test_smells_output_marks_run_as_failed_even_when_cli_exit_code_is_zero(
        self,
    ) -> None:
        """Cover smells output marks run as failed even when cli exit code is zero."""
        completed_check = type(
            "Completed", (), {"returncode": 0, "stdout": "clean\n", "stderr": ""}
        )()
        completed_smells = type(
            "Completed", (), {"returncode": 0, "stdout": "one smell\n", "stderr": ""}
        )()
        result, _repo_dir, json_text, _markdown, _call_args = (
            self._run_main_with_completed_processes(
                completed_check,
                completed_smells,
            )
        )

        self.assertEqual(result, 1)
        payload = json.loads(json_text)
        self.assertEqual(payload["status"], "fail")
        self.assertEqual(payload["return_code"], 1)
        self.assertEqual(payload["checks"][1]["status"], "fail")
        self.assertIn("one smell", payload["checks"][1]["output_tail"])

    def test_main_returns_success_and_still_writes_artifacts_for_clean_check(
        self,
    ) -> None:
        """Cover main returns success and still writes artifacts for clean check."""
        completed_check = type(
            "Completed", (), {"returncode": 0, "stdout": "clean\n", "stderr": ""}
        )()
        completed_smells = type(
            "Completed", (), {"returncode": 0, "stdout": "no smells\n", "stderr": ""}
        )()
        result, _repo_dir, json_text, markdown, _call_args = (
            self._run_main_with_completed_processes(
                completed_check,
                completed_smells,
            )
        )

        self.assertEqual(result, 0)
        payload = json.loads(json_text)
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["return_code"], 0)
        self.assertEqual(len(payload["checks"]), 2)
        self.assertEqual(markdown.count("QLTY Zero"), 1)

    def test_reloading_the_module_without_the_repo_root_reinserts_the_import_path(
        self,
    ) -> None:
        """Cover reloading the module without the repo root reinserts the import path."""
        repo_root = str(Path(run_qlty_zero.__file__).resolve().parents[2])
        original_sys_path = list(sys.path)
        try:
            while repo_root in sys.path:
                sys.path.remove(repo_root)
            importlib.reload(run_qlty_zero)
            self.assertIn(repo_root, sys.path)
        finally:
            sys.path[:] = original_sys_path
