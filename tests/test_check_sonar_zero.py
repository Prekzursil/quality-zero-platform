from __future__ import absolute_import

import argparse
import os
import runpy
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from scripts.quality import check_sonar_zero
from scripts.quality.check_sonar_zero import load_sonar_findings_with_retry
from typing import List


class SonarZeroTests(unittest.TestCase):
    def test_auth_header_and_query_helpers_cover_scoped_inputs(self) -> None:
        auth_header = check_sonar_zero._auth_header("token")
        self.assertTrue(auth_header.startswith("Basic "))
        self.assertEqual(
            check_sonar_zero._build_sonar_query("project", branch="main", pull_request="5"),
            {"projectKey": "project", "branch": "main", "pullRequest": "5"},
        )

    def test_request_json_rejects_non_dict_payloads(self) -> None:
        with patch("scripts.quality.check_sonar_zero.load_json_https", return_value=(["invalid"], {})):
            with self.assertRaisesRegex(RuntimeError, "Unexpected SonarCloud API response payload"):
                check_sonar_zero._request_json("https://sonarcloud.io/api/issues/search", "auth")
        with patch("scripts.quality.check_sonar_zero.load_json_https", return_value=({"paging": {"total": 0}}, {})):
            self.assertEqual(
                check_sonar_zero._request_json("https://sonarcloud.io/api/issues/search", "auth"),
                {"paging": {"total": 0}},
            )

    def test_load_helpers_collect_open_issues_quality_gate_and_findings(self) -> None:
        responses = [
            {"paging": {"total": 1}},
            {"projectStatus": {"status": "ERROR"}},
        ]
        captured_urls: List[str] = []

        def fake_request(url: str, auth_header: str):
            captured_urls.append(url)
            return responses.pop(0)

        args = Namespace(project_key="project", branch="", pull_request="5")
        with patch("scripts.quality.check_sonar_zero._request_json", side_effect=fake_request):
            self.assertEqual(check_sonar_zero._load_open_issues(args, "auth"), 1)
            self.assertEqual(check_sonar_zero._load_quality_gate(args, "auth"), "ERROR")

        self.assertIn("pullRequest=5", captured_urls[0])
        self.assertIn("pullRequest=5", captured_urls[1])

        branch_urls: List[str] = []

        def fake_branch_request(url: str, auth_header: str):
            branch_urls.append(url)
            return {"paging": {"total": 0}}

        branch_args = Namespace(project_key="project", branch="main", pull_request="")
        with patch("scripts.quality.check_sonar_zero._request_json", side_effect=fake_branch_request):
            self.assertEqual(check_sonar_zero._load_open_issues(branch_args, "auth"), 0)

        self.assertIn("branch=main", branch_urls[0])

        with patch.object(check_sonar_zero, "_load_open_issues", return_value=2), patch.object(
            check_sonar_zero, "_load_quality_gate", return_value="ERROR"
        ):
            open_issues, quality_gate, findings = check_sonar_zero._load_sonar_findings(args, "auth")
        self.assertEqual(open_issues, 2)
        self.assertEqual(quality_gate, "ERROR")
        self.assertIn("Sonar reports 2 open issues", findings[0])
        self.assertIn("Sonar quality gate status is ERROR", findings[1])

        with patch.object(check_sonar_zero, "_load_open_issues", return_value=0), patch.object(
            check_sonar_zero, "_load_quality_gate", return_value="OK"
        ):
            self.assertEqual(check_sonar_zero._load_sonar_findings(args, "auth"), (0, "OK", []))

    def test_retry_waits_for_pr_scoped_findings_to_settle(self) -> None:
        args = argparse.Namespace(branch="", pull_request="5")
        responses = [
            (1, "OK", ["Sonar reports 1 open issues (expected 0)."]),
            (0, "OK", []),
        ]
        attempts: List[int] = []

        def fake_loader(current_args, auth):
            attempts.append(len(attempts) + 1)
            return responses.pop(0)

        open_issues, quality_gate, findings = load_sonar_findings_with_retry(
            args,
            "auth",
            fetch_fn=fake_loader,
            attempts=2,
            sleep_seconds=0.0,
        )

        self.assertEqual((open_issues, quality_gate, findings), (0, "OK", []))
        self.assertEqual(attempts, [1, 2])

    def test_retry_keyword_only_guards_reject_invalid_invocations(self) -> None:
        args = argparse.Namespace(branch="", pull_request="5")

        with self.assertRaisesRegex(TypeError, "expects argparse namespace and auth header"):
            load_sonar_findings_with_retry(args)

        with self.assertRaisesRegex(TypeError, "Unexpected load_sonar_findings_with_retry parameters: extra"):
            load_sonar_findings_with_retry(
                args,
                "auth",
                fetch_fn=lambda _args, _auth: (0, "OK", []),
                extra=True,
            )

    def test_retry_skips_unscoped_queries(self) -> None:
        args = argparse.Namespace(branch="", pull_request="")
        attempts: List[int] = []

        def fake_loader(current_args, auth):
            attempts.append(len(attempts) + 1)
            return 3, "ERROR", ["Sonar reports 3 open issues (expected 0)."]

        open_issues, quality_gate, findings = load_sonar_findings_with_retry(
            args,
            "auth",
            fetch_fn=fake_loader,
            attempts=3,
            sleep_seconds=0.0,
        )

        self.assertEqual((open_issues, quality_gate, findings), (3, "ERROR", ["Sonar reports 3 open issues (expected 0)."]))
        self.assertEqual(attempts, [1])

    def test_retry_returns_last_scoped_result_after_retry_budget_is_exhausted(self) -> None:
        args = argparse.Namespace(branch="", pull_request="5")
        attempts: List[int] = []

        def fake_loader(current_args, auth):
            attempts.append(len(attempts) + 1)
            return 1, "ERROR", ["Sonar reports 1 open issues (expected 0)."]

        open_issues, quality_gate, findings = load_sonar_findings_with_retry(
            args,
            "auth",
            fetch_fn=fake_loader,
            attempts=2,
            sleep_seconds=0.0,
        )

        self.assertEqual((open_issues, quality_gate, findings), (1, "ERROR", ["Sonar reports 1 open issues (expected 0)."]))
        self.assertEqual(attempts, [1, 2])

    def test_retry_default_budget_handles_transient_none_quality_gate_for_prs(self) -> None:
        args = argparse.Namespace(branch="", pull_request="13")
        attempts: List[int] = []
        responses = [
            (0, "NONE", ["Sonar quality gate status is NONE (expected OK)."]),
            (0, "NONE", ["Sonar quality gate status is NONE (expected OK)."]),
            (0, "NONE", ["Sonar quality gate status is NONE (expected OK)."]),
            (0, "NONE", ["Sonar quality gate status is NONE (expected OK)."]),
            (0, "NONE", ["Sonar quality gate status is NONE (expected OK)."]),
            (0, "NONE", ["Sonar quality gate status is NONE (expected OK)."]),
            (0, "NONE", ["Sonar quality gate status is NONE (expected OK)."]),
            (0, "OK", []),
        ]

        def fake_loader(current_args, auth):
            attempts.append(len(attempts) + 1)
            return responses.pop(0)

        open_issues, quality_gate, findings = load_sonar_findings_with_retry(
            args,
            "auth",
            fetch_fn=fake_loader,
            sleep_seconds=0.0,
        )

        self.assertEqual((open_issues, quality_gate, findings), (0, "OK", []))
        self.assertEqual(attempts, [1, 2, 3, 4, 5, 6, 7, 8])

    def test_main_handles_missing_token_success_and_report_failures(self) -> None:
        args = Namespace(
            project_key="Prekzursil_quality-zero-platform",
            token=str(),
            branch="",
            pull_request="5",
            out_json="sonar-zero/sonar.json",
            out_md="sonar-zero/sonar.md",
        )

        with patch.dict("os.environ", {}, clear=True), patch.object(check_sonar_zero, "_parse_args", return_value=args), patch.object(
            check_sonar_zero, "write_report", return_value=0
        ) as write_report_mock:
            self.assertEqual(check_sonar_zero.main(), 1)
        self.assertEqual(write_report_mock.call_args.args[0]["findings"], ["SONAR_TOKEN is missing."])

        success_args = Namespace(**{**args.__dict__, "token": "provided-token"})
        with patch.object(check_sonar_zero, "_parse_args", return_value=success_args), patch.object(
            check_sonar_zero, "load_sonar_findings_with_retry", return_value=(0, "OK", [])
        ), patch.object(check_sonar_zero, "write_report", return_value=0) as write_report_mock:
            self.assertEqual(check_sonar_zero.main(), 0)
        self.assertEqual(write_report_mock.call_args.args[0]["status"], "pass")

        with patch.object(check_sonar_zero, "_parse_args", return_value=success_args), patch.object(
            check_sonar_zero,
            "load_sonar_findings_with_retry",
            side_effect=RuntimeError("provider timeout"),
        ), patch.object(check_sonar_zero, "write_report", return_value=0) as write_report_mock:
            self.assertEqual(check_sonar_zero.main(), 1)
        self.assertEqual(
            write_report_mock.call_args.args[0]["findings"],
            ["Sonar API request failed: provider timeout"],
        )

        with patch.object(check_sonar_zero, "_parse_args", return_value=success_args), patch.object(
            check_sonar_zero, "load_sonar_findings_with_retry", return_value=(0, "OK", [])
        ), patch.object(check_sonar_zero, "write_report", return_value=4):
            self.assertEqual(check_sonar_zero.main(), 4)

    def test_parse_args_render_markdown_and_script_entrypoint(self) -> None:
        with patch.object(sys, "argv", ["check_sonar_zero.py", "--project-key", "project"]):
            args = check_sonar_zero._parse_args()
        self.assertEqual(args.project_key, "project")
        markdown = check_sonar_zero._render_md(
            {
                "status": "pass",
                "project_key": "project",
                "open_issues": 0,
                "quality_gate": "OK",
                "timestamp_utc": "2026-03-15T00:00:00+00:00",
                "findings": [],
            }
        )
        self.assertIn("- None", markdown)

        script_path = Path("scripts/quality/check_sonar_zero.py").resolve()
        root_text = str(Path.cwd().resolve())
        trimmed_sys_path = [item for item in sys.path if item != root_text]
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True), patch.object(
            sys,
            "argv",
            [str(script_path), "--project-key", "Prekzursil_quality-zero-platform"],
        ), patch.object(sys, "path", trimmed_sys_path[:]):
            cwd = Path(tmp)
            previous = Path.cwd()
            os.chdir(cwd)
            try:
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(script_path), run_name="__main__")
            finally:
                os.chdir(previous)
        self.assertEqual(result.exception.code, 1)

