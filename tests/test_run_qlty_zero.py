from __future__ import absolute_import

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.quality.run_qlty_zero as run_qlty_zero


class RunQltyZeroTests(unittest.TestCase):
    def test_build_qlty_check_argv_uses_fail_on_any_issue_semantics(self) -> None:
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
        self.assertEqual(
            run_qlty_zero._build_qlty_smells_argv(),
            [
                "qlty",
                "smells",
                "--all",
                "--quiet",
                "--no-snippets",
                "--no-duplication",
            ],
        )

    def test_main_runs_qlty_check_in_repo_root_and_writes_failure_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir) / "repo"
            repo_dir.mkdir()
            stdout_path = repo_dir / "qlty-zero" / "qlty-zero.json"
            md_path = repo_dir / "qlty-zero" / "qlty-zero.md"
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

            with (
                patch(
                    "scripts.quality.run_qlty_zero.subprocess.run",
                    side_effect=[completed_check, completed_smells],
                ) as mock_run,
                patch("scripts.quality.run_qlty_zero.sys.argv", [
                    "run_qlty_zero.py",
                    "--repo-dir",
                    str(repo_dir),
                    "--out-json",
                    str(stdout_path),
                    "--out-md",
                    str(md_path),
                ]),
            ):
                result = run_qlty_zero.main()

            self.assertEqual(result, 1)
            self.assertEqual(mock_run.call_count, 2)
            self.assertEqual(
                mock_run.call_args_list[0].args[0],
                ["qlty", "check", "--all", "--fail-level", "note", "--summary"],
            )
            self.assertEqual(
                mock_run.call_args_list[1].args[0],
                ["qlty", "smells", "--all", "--quiet", "--no-snippets", "--no-duplication"],
            )
            for call in mock_run.call_args_list:
                self.assertEqual(Path(call.kwargs["cwd"]).resolve(), repo_dir.resolve())
                self.assertFalse(call.kwargs.get("shell", False))
                self.assertFalse(call.kwargs["check"])
                self.assertTrue(call.kwargs["capture_output"])
                self.assertTrue(call.kwargs["text"])
            self.assertTrue(stdout_path.exists())
            self.assertTrue(md_path.exists())

            payload = json.loads(stdout_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "fail")
            self.assertEqual(payload["return_code"], 1)
            self.assertEqual(
                payload["commands"],
                [
                    ["qlty", "check", "--all", "--fail-level", "note", "--summary"],
                    ["qlty", "smells", "--all", "--quiet", "--no-snippets", "--no-duplication"],
                ],
            )
            self.assertEqual(payload["checks"][0]["name"], "check")
            self.assertEqual(payload["checks"][1]["name"], "smells")
            self.assertIn("smell summary", payload["output_tail"])
            self.assertIn("stderr noise", payload["output_tail"])
            self.assertIn("QLTY Zero", md_path.read_text(encoding="utf-8"))
            self.assertIn("## check", md_path.read_text(encoding="utf-8"))
            self.assertIn("## smells", md_path.read_text(encoding="utf-8"))

    def test_smells_output_marks_run_as_failed_even_when_cli_exit_code_is_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir) / "repo"
            repo_dir.mkdir()
            json_path = repo_dir / "qlty-zero" / "qlty-zero.json"
            md_path = repo_dir / "qlty-zero" / "qlty-zero.md"
            completed_check = type("Completed", (), {"returncode": 0, "stdout": "clean\n", "stderr": ""})()
            completed_smells = type("Completed", (), {"returncode": 0, "stdout": "one smell\n", "stderr": ""})()

            with (
                patch(
                    "scripts.quality.run_qlty_zero.subprocess.run",
                    side_effect=[completed_check, completed_smells],
                ),
                patch("scripts.quality.run_qlty_zero.sys.argv", [
                    "run_qlty_zero.py",
                    "--repo-dir",
                    str(repo_dir),
                    "--out-json",
                    str(json_path),
                    "--out-md",
                    str(md_path),
                ]),
            ):
                result = run_qlty_zero.main()

            self.assertEqual(result, 1)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "fail")
            self.assertEqual(payload["return_code"], 1)
            self.assertEqual(payload["checks"][1]["status"], "fail")
            self.assertIn("one smell", payload["checks"][1]["output_tail"])

    def test_main_returns_success_and_still_writes_artifacts_for_clean_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir) / "repo"
            repo_dir.mkdir()
            json_path = repo_dir / "qlty-zero" / "qlty-zero.json"
            md_path = repo_dir / "qlty-zero" / "qlty-zero.md"
            completed_check = type("Completed", (), {"returncode": 0, "stdout": "clean\n", "stderr": ""})()
            completed_smells = type("Completed", (), {"returncode": 0, "stdout": "no smells\n", "stderr": ""})()

            with (
                patch(
                    "scripts.quality.run_qlty_zero.subprocess.run",
                    side_effect=[completed_check, completed_smells],
                ),
                patch("scripts.quality.run_qlty_zero.sys.argv", [
                    "run_qlty_zero.py",
                    "--repo-dir",
                    str(repo_dir),
                    "--out-json",
                    str(json_path),
                    "--out-md",
                    str(md_path),
                ]),
            ):
                result = run_qlty_zero.main()

            self.assertEqual(result, 0)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["return_code"], 0)
            self.assertEqual(len(payload["checks"]), 2)
            self.assertEqual(md_path.read_text(encoding="utf-8").count("QLTY Zero"), 1)
