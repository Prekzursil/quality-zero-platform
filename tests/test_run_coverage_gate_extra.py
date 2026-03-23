from __future__ import absolute_import

import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch
import zipfile

from scripts.quality import run_coverage_gate


class RunCoverageGateExtraTests(unittest.TestCase):
    def test_download_and_lookup_helpers_cover_success_paths(self) -> None:
        with patch.object(run_coverage_gate, "load_bytes_https", return_value=(b"bytes", {})) as load_bytes_mock:
            self.assertEqual(run_coverage_gate._download_bytes("https://api.github.com/example", "token"), b"bytes")
        self.assertEqual(load_bytes_mock.call_args.kwargs["allowed_hosts"], {"api.github.com"})

        with patch.dict("os.environ", {"GITHUB_TOKEN": "token"}, clear=True):
            self.assertEqual(run_coverage_gate._github_api_token(), "token")
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GITHUB_TOKEN or GH_TOKEN is required"):
                run_coverage_gate._github_api_token()

        runs = [{"id": 1, "name": "Other", "conclusion": "success"}, {"id": 2, "name": "Quality Zero Platform", "conclusion": "success"}]
        self.assertEqual(run_coverage_gate._find_successful_run_id(runs, "Quality Zero Platform"), 2)
        self.assertIsNone(run_coverage_gate._find_successful_run_id(runs, "Missing"))
        artifacts = [{"name": "coverage-artifacts", "archive_download_url": "https://api.github.com/archive.zip"}]
        self.assertEqual(run_coverage_gate._find_artifact_by_name(artifacts, "coverage-artifacts"), artifacts[0])
        self.assertIsNone(run_coverage_gate._find_artifact_by_name(artifacts, "missing"))

    def test_baseline_loading_and_non_regression_reporting_cover_success_and_failure(self) -> None:
        baseline_zip = io.BytesIO()
        with zipfile.ZipFile(baseline_zip, "w") as handle:
            handle.writestr("coverage-100/coverage.json", json.dumps({"components": [{"covered": 5, "total": 10}]}))

        downloads = [
            json.dumps({"workflow_runs": [{"id": 2, "name": "Quality Zero Platform", "conclusion": "success"}]}).encode("utf-8"),
            json.dumps({"artifacts": [{"name": "coverage-artifacts", "archive_download_url": "https://api.github.com/archive.zip"}]}).encode("utf-8"),
            baseline_zip.getvalue(),
        ]
        with patch.object(run_coverage_gate, "_github_api_token", return_value="token"), patch.object(run_coverage_gate, "_download_bytes", side_effect=downloads):
            payload = run_coverage_gate._load_baseline_coverage_payload({"slug": "owner/repo", "default_branch": "main"})
        self.assertEqual(payload["components"][0]["covered"], 5)

        markdown = run_coverage_gate._render_non_regression_md(
            {"status": "fail", "current_percent": 90.0, "baseline_percent": 95.0, "timestamp_utc": "now", "findings": ["regressed"]}
        )
        self.assertIn("non_regression", markdown)
        self.assertIn("regressed", markdown)

        with patch.object(run_coverage_gate, "write_report", return_value=0) as write_report_mock:
            self.assertEqual(
                run_coverage_gate._write_non_regression_report(
                    {"components": [{"covered": 9, "total": 10}]},
                    {"components": [{"covered": 8, "total": 10}]},
                ),
                0,
            )
        self.assertEqual(write_report_mock.call_args.args[0]["status"], "pass")

        with patch.object(run_coverage_gate, "write_report", return_value=0) as write_report_mock:
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

    def test_main_uses_default_platform_dir_and_non_regression_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile_json = root / "profile.json"
            profile_json.write_text(
                json.dumps(
                    {
                        "slug": "owner/repo",
                        "default_branch": "main",
                        "coverage": {"command": "", "shell": "bash", "assert_mode": {"default": "non_regression"}, "inputs": []},
                    }
                ),
                encoding="utf-8",
            )
            args = Namespace(profile_json=str(profile_json), event_name="pull_request", repo_dir=".", platform_dir="")
            with (
                patch.object(run_coverage_gate, "_parse_args", return_value=args),
                patch.object(run_coverage_gate, "_run_shell") as run_shell_mock,
                patch.object(run_coverage_gate, "_collect_current_coverage_payload", return_value={"components": [{"covered": 9, "total": 10}]}),
                patch.object(run_coverage_gate, "_load_baseline_coverage_payload", return_value={"components": [{"covered": 8, "total": 10}]}),
                patch.object(run_coverage_gate, "_write_non_regression_report", return_value=0) as write_report_mock,
            ):
                self.assertEqual(run_coverage_gate.main(), 0)
        run_shell_mock.assert_called_once()
        write_report_mock.assert_called_once()
