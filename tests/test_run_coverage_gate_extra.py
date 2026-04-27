"""Test run coverage gate -- non-regression, baseline, and entrypoint paths."""


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


from scripts.quality import run_coverage_gate




from tests._run_coverage_gate_helpers import (
    assert_run_shell_invocation,
    make_coverage_assert_fixture,
)


class RunCoverageGateExtraTests(unittest.TestCase):
    """RunCoverageGateExtraTests."""

    def _assert_run_shell_invocation(
        self,
        *,
        shell_name: str,
        resolved_path: str,
        expected_argv: List[str],
    ) -> None:
        return assert_run_shell_invocation(
            self,
            shell_name=shell_name,
            resolved_path=resolved_path,
            expected_argv=expected_argv,
        )

    @staticmethod
    def _coverage_assert_fixture():
        return make_coverage_assert_fixture()


    def test_non_regression_mode_uses_baseline_payload(self) -> None:
        """Cover non regression mode uses baseline payload."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            profile_json = temp_path / "profile.json"
            profile_json.write_text(
                json.dumps(
                    {
                        "slug": "Prekzursil/SWFOC-Mod-Menu",
                        "default_branch": "main",
                        "coverage": {
                            "command": "",
                            "shell": "bash",
                            "assert_mode": {"default": "non_regression"},
                            "inputs": [],
                        },
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch("scripts.quality.run_coverage_gate._run_shell") as mock_shell,
                patch(
                    (
                        "scripts.quality.run_coverage_gate."
                        "_collect_current_coverage_payload"
                    ),
                    return_value={"combined_percent": 95.0},
                ),
                patch(
                    "scripts.quality.run_coverage_gate._load_baseline_coverage_payload",
                    return_value={"combined_percent": 94.0},
                ),
                patch(
                    "scripts.quality.run_coverage_gate._write_non_regression_report",
                    return_value=0,
                ) as mock_report,
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_coverage_gate.py",
                        "--profile-json",
                        str(profile_json),
                        "--event-name",
                        "pull_request",
                    ],
                ),
            ):
                result = run_coverage_gate.main()

        self.assertEqual(result, 0)
        mock_shell.assert_called_once()
        mock_report.assert_called_once()

    def test_download_and_lookup_helpers_cover_success_paths(self) -> None:
        """Cover download and lookup helpers cover success paths."""
        with patch.object(
            run_coverage_gate, "load_bytes_https", return_value=(b"bytes", {})
        ) as load_bytes_mock:
            self.assertEqual(
                run_coverage_gate._download_bytes(
                    "https://api.github.com/example", "token"
                ),
                b"bytes",
            )
        self.assertEqual(
            load_bytes_mock.call_args.kwargs["allowed_hosts"], {"api.github.com"}
        )

        with patch.dict("os.environ", {"GITHUB_TOKEN": "token"}, clear=True):
            self.assertEqual(run_coverage_gate._github_api_token(), "token")
        with (
            patch.dict("os.environ", {}, clear=True),
            self.assertRaisesRegex(
                RuntimeError, "GITHUB_TOKEN or GH_TOKEN is required"
            ),
        ):
            run_coverage_gate._github_api_token()

        runs = [
            {"id": 1, "name": "Other", "conclusion": "success"},
            {"id": 2, "name": "Quality Zero Platform", "conclusion": "success"},
        ]
        self.assertEqual(
            run_coverage_gate._find_successful_run_id(runs, "Quality Zero Platform"), 2
        )
        self.assertIsNone(run_coverage_gate._find_successful_run_id(runs, "Missing"))
        artifacts = [
            {
                "name": "coverage-artifacts",
                "archive_download_url": "https://api.github.com/archive.zip",
            }
        ]
        self.assertEqual(
            run_coverage_gate._find_artifact_by_name(artifacts, "coverage-artifacts"),
            artifacts[0],
        )
        self.assertIsNone(
            run_coverage_gate._find_artifact_by_name(artifacts, "missing")
        )

    def test_baseline_loading_reads_artifact_payload(self) -> None:
        """Cover baseline loading reads artifact payload."""
        baseline_zip = io.BytesIO()
        with zipfile.ZipFile(baseline_zip, "w") as handle:
            handle.writestr(
                "coverage-100/coverage.json",
                json.dumps({"components": [{"covered": 5, "total": 10}]}),
            )

        downloads = [
            json.dumps(
                {
                    "workflow_runs": [
                        {
                            "id": 2,
                            "name": "Quality Zero Platform",
                            "conclusion": "success",
                        }
                    ]
                }
            ).encode("utf-8"),
            json.dumps(
                {
                    "artifacts": [
                        {
                            "name": "coverage-artifacts",
                            "archive_download_url": (
                                "https://api.github.com/archive.zip"
                            ),
                        }
                    ]
                }
            ).encode("utf-8"),
            baseline_zip.getvalue(),
        ]
        with (
            patch.object(
                run_coverage_gate,
                "_github_api_token",
                return_value="token",
            ),
            patch.object(
                run_coverage_gate,
                "_download_bytes",
                side_effect=downloads,
            ),
        ):
            payload = run_coverage_gate._load_baseline_coverage_payload(
                {"slug": "owner/repo", "default_branch": "main"}
            )
        self.assertEqual(payload["components"][0]["covered"], 5)

    def test_non_regression_markdown_mentions_regressions(self) -> None:
        """Cover non regression markdown mentions regressions."""
        markdown = run_coverage_gate._render_non_regression_md(
            {
                "status": "fail",
                "current_percent": 90.0,
                "baseline_percent": 95.0,
                "timestamp_utc": "now",
                "findings": ["regressed"],
            }
        )
        self.assertIn("non_regression", markdown)
        self.assertIn("regressed", markdown)

    def test_write_non_regression_report_handles_pass_fail_and_write_errors(
        self,
    ) -> None:
        """Cover write non regression report handles pass fail and write errors."""
        with patch.object(
            run_coverage_gate, "write_report", return_value=0
        ) as write_report_mock:
            self.assertEqual(
                run_coverage_gate._write_non_regression_report(
                    {"components": [{"covered": 9, "total": 10}]},
                    {"components": [{"covered": 8, "total": 10}]},
                ),
                0,
            )
        self.assertEqual(write_report_mock.call_args.args[0]["status"], "pass")

        with patch.object(
            run_coverage_gate, "write_report", return_value=0
        ) as write_report_mock:
            self.assertEqual(
                run_coverage_gate._write_non_regression_report(
                    {"components": [{"covered": 7, "total": 10}]},
                    {"components": [{"covered": 8, "total": 10}]},
                ),
                1,
            )
        self.assertEqual(write_report_mock.call_args.args[0]["status"], "fail")

        with patch.object(run_coverage_gate, "write_report", return_value=6):
            self.assertEqual(
                run_coverage_gate._write_non_regression_report(
                    {"components": [{"covered": 9, "total": 10}]},
                    {"components": [{"covered": 8, "total": 10}]},
                ),
                6,
            )

    def test_remaining_helper_and_entrypoint_paths(self) -> None:
        """Cover remaining helper and entrypoint paths."""
        self.assertEqual(
            run_coverage_gate._combined_coverage_percent({"components": "bad"}), 100.0
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "coverage-100").mkdir()
            (repo_dir / "coverage-100" / "coverage.json").write_text(
                "{}", encoding="utf-8"
            )
            with patch.object(
                run_coverage_gate, "_run_assert_coverage_100", return_value=0
            ):
                self.assertEqual(
                    run_coverage_gate._collect_current_coverage_payload(
                        {}, repo_dir=repo_dir, platform_dir=repo_dir
                    ),
                    {},
                )
            with (
                patch.object(
                    run_coverage_gate, "_run_assert_coverage_100", return_value=2
                ),
                self.assertRaisesRegex(
                    RuntimeError, "coverage assertion returned unexpected exit code 2"
                ),
            ):
                run_coverage_gate._collect_current_coverage_payload(
                    {}, repo_dir=repo_dir, platform_dir=repo_dir
                )

        with (
            patch.object(run_coverage_gate, "_github_api_token", return_value="token"),
            patch.object(
                run_coverage_gate,
                "_download_bytes",
                return_value=json.dumps({"workflow_runs": []}).encode("utf-8"),
            ),
            self.assertRaisesRegex(
                RuntimeError, "Unable to find a successful Quality Zero Platform run"
            ),
        ):
            run_coverage_gate._load_baseline_coverage_payload(
                {"slug": "owner/repo", "default_branch": "main"}
            )

        with (
            patch.object(run_coverage_gate, "_github_api_token", return_value="token"),
            patch.object(
                run_coverage_gate,
                "_download_bytes",
                side_effect=[
                    json.dumps(
                        {
                            "workflow_runs": [
                                {
                                    "id": 1,
                                    "name": "Quality Zero Platform",
                                    "conclusion": "success",
                                }
                            ]
                        }
                    ).encode("utf-8"),
                    json.dumps({"artifacts": []}).encode("utf-8"),
                ],
            ),
            self.assertRaisesRegex(RuntimeError, "Unable to find coverage-artifacts"),
        ):
            run_coverage_gate._load_baseline_coverage_payload(
                {"slug": "owner/repo", "default_branch": "main"}
            )

        script_path = Path(run_coverage_gate.__file__).resolve()
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_json = Path(temp_dir) / "profile.json"
            profile_json.write_text(
                json.dumps(
                    {
                        "coverage": {
                            "command": "",
                            "shell": "bash",
                            "assert_mode": {"default": "evidence_only"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        str(script_path),
                        "--profile-json",
                        str(profile_json),
                        "--event-name",
                        "push",
                    ],
                ),
                self.assertRaises(SystemExit) as result,
            ):
                runpy.run_path(str(script_path), run_name="__main__")
            self.assertEqual(result.exception.code, 0)
