"""Test check sonar zero."""

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
    """Sonar Zero Tests."""

    def test_auth_header_and_query_helpers_cover_scoped_inputs(self) -> None:
        """Cover auth header and query helpers cover scoped inputs."""
        auth_header = check_sonar_zero._auth_header("token")
        self.assertTrue(auth_header.startswith("Basic "))
        self.assertEqual(
            check_sonar_zero._build_sonar_query("project", branch="main", pull_request="5"),
            {"projectKey": "project", "branch": "main", "pullRequest": "5"},
        )

    def test_request_json_rejects_non_dict_payloads(self) -> None:
        """Cover request json rejects non dict payloads."""
        with patch("scripts.quality.check_sonar_zero.load_json_https", return_value=(["invalid"], {})):
            with self.assertRaisesRegex(RuntimeError, "Unexpected SonarCloud API response payload"):
                check_sonar_zero._request_json("https://sonarcloud.io/api/issues/search", "auth")
        with patch("scripts.quality.check_sonar_zero.load_json_https", return_value=({"paging": {"total": 0}}, {})):
            self.assertEqual(
                check_sonar_zero._request_json("https://sonarcloud.io/api/issues/search", "auth"),
                {"paging": {"total": 0}},
            )

    def test_load_helpers_collect_open_issues_quality_gate_and_findings(self) -> None:
        """Cover load helpers collect open issues quality gate and findings."""
        responses = [
            {"paging": {"total": 1}},
            {"projectStatus": {"status": "ERROR"}},
        ]
        captured_urls: List[str] = []

        def fake_request(url: str, auth_header: str):
            """Handle fake request."""
            captured_urls.append(url)
            return responses.pop(0)

        args = Namespace(project_key="project", branch="", pull_request="5", policy_mode="zero")
        with patch("scripts.quality.check_sonar_zero._request_json", side_effect=fake_request):
            self.assertEqual(check_sonar_zero._load_open_issues(args, "auth"), 1)
            self.assertEqual(check_sonar_zero._load_quality_gate(args, "auth"), "ERROR")

        self.assertIn("pullRequest=5", captured_urls[0])
        self.assertIn("pullRequest=5", captured_urls[1])

        branch_urls: List[str] = []

        def fake_branch_request(url: str, auth_header: str):
            """Handle fake branch request."""
            branch_urls.append(url)
            return {"paging": {"total": 0}}

        branch_args = Namespace(project_key="project", branch="main", pull_request="", policy_mode="zero")
        with patch("scripts.quality.check_sonar_zero._request_json", side_effect=fake_branch_request):
            self.assertEqual(check_sonar_zero._load_open_issues(branch_args, "auth"), 0)

        self.assertIn("branch=main", branch_urls[0])

        with patch.object(check_sonar_zero, "_load_open_issues", return_value=2), patch.object(check_sonar_zero, "_load_quality_gate", return_value="ERROR"):
            open_issues, quality_gate, findings = check_sonar_zero._load_sonar_findings(args, "auth")
        self.assertEqual(open_issues, 2)
        self.assertEqual(quality_gate, "ERROR")
        self.assertIn("Sonar reports 2 open issues", findings[0])
        self.assertIn("Sonar quality gate status is ERROR", findings[1])

        with patch.object(check_sonar_zero, "_load_open_issues", return_value=0), patch.object(check_sonar_zero, "_load_quality_gate", return_value="OK"):
            self.assertEqual(check_sonar_zero._load_sonar_findings(args, "auth"), (0, "OK", []))

        ratchet_args = Namespace(project_key="project", branch="", pull_request="5", policy_mode="ratchet")
        with patch.object(check_sonar_zero, "_load_open_issues", return_value=6), patch.object(check_sonar_zero, "_load_quality_gate", return_value="OK"):
            self.assertEqual(check_sonar_zero._load_sonar_findings(ratchet_args, "auth"), (6, "OK", []))

    def test_revision_helpers_and_pending_message_cover_scoped_paths(self) -> None:
        """Cover revision helpers and pending message cover scoped paths."""
        args = Namespace(project_key="project", branch="main", pull_request="", sha="targetsha")
        with patch.object(
            check_sonar_zero,
            "_request_json",
            return_value={"branches": [{"name": "main", "commit": {"sha": "oldsha"}}]},
        ):
            self.assertEqual(check_sonar_zero._load_branch_analysis_revision(args, "auth"), "oldsha")
            self.assertEqual(
                check_sonar_zero._scoped_analysis_pending_message(args, "auth"),
                "Sonar analysis for branch main is still on oldsha (waiting for targetsha).",
            )

        with patch.object(
            check_sonar_zero,
            "_request_json",
            return_value={"branches": [{"name": "other", "commit": {"sha": "oldsha"}}]},
        ):
            self.assertEqual(check_sonar_zero._load_branch_analysis_revision(args, "auth"), "")
            self.assertEqual(
                check_sonar_zero._scoped_analysis_pending_message(args, "auth"),
                "Sonar analysis for branch main is not available yet.",
            )

        pr_args = Namespace(project_key="project", branch="", pull_request="5", sha="targetsha")
        with patch.object(
            check_sonar_zero,
            "_request_json",
            return_value={"pullRequests": [{"key": "5", "commit": {"sha": "targetsha"}}]},
        ):
            self.assertEqual(check_sonar_zero._load_pull_request_analysis_revision(pr_args, "auth"), "targetsha")
            self.assertIsNone(check_sonar_zero._scoped_analysis_pending_message(pr_args, "auth"))
        with patch.object(
            check_sonar_zero,
            "_request_json",
            return_value={"pullRequests": [{"key": "other", "commit": {"sha": "oldsha"}}]},
        ):
            self.assertEqual(check_sonar_zero._load_pull_request_analysis_revision(pr_args, "auth"), "")

        self.assertIsNone(
            check_sonar_zero._scoped_analysis_pending_message(
                Namespace(project_key="project", branch="", pull_request="", sha=""),
                "auth",
            )
        )

    def test_retry_waits_for_pr_scoped_findings_to_settle(self) -> None:
        """Cover retry waits for pr scoped findings to settle."""
        args = argparse.Namespace(branch="", pull_request="5")
        responses = [
            (1, "OK", ["Sonar reports 1 open issues (expected 0)."]),
            (0, "OK", []),
        ]
        attempts: List[int] = []

        def fake_loader(current_args, auth):
            """Handle fake loader."""
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

    def test_retry_waits_for_scoped_revision_to_match_target_sha(self) -> None:
        """Cover retry waits for scoped revision to match target sha."""
        args = argparse.Namespace(branch="main", pull_request="", sha="targetsha")
        attempts: List[int] = []
        pending_responses = [
            "Sonar analysis for branch main is still on oldsha (waiting for targetsha).",
            None,
        ]

        def fake_loader(current_args, auth):
            """Handle fake loader."""
            attempts.append(len(attempts) + 1)
            return 0, "OK", []

        def fake_pending(current_args, auth):
            """Handle fake pending."""
            return pending_responses.pop(0)

        open_issues, quality_gate, findings = load_sonar_findings_with_retry(
            args,
            "auth",
            fetch_fn=fake_loader,
            pending_fn=fake_pending,
            attempts=2,
            sleep_seconds=0.0,
        )

        self.assertEqual((open_issues, quality_gate, findings), (0, "OK", []))
        self.assertEqual(attempts, [1, 2])

    def test_retry_retries_transient_scoped_api_errors_before_failing(self) -> None:
        """Cover retry retries transient scoped api errors before failing."""
        args = argparse.Namespace(branch="", pull_request="5")
        attempts: List[int] = []

        def fake_loader(current_args, auth):
            """Handle fake loader."""
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                raise RuntimeError("HTTP Error 404")
            return 0, "OK", []

        open_issues, quality_gate, findings = load_sonar_findings_with_retry(
            args,
            "auth",
            fetch_fn=fake_loader,
            attempts=2,
            sleep_seconds=0.0,
        )

        self.assertEqual((open_issues, quality_gate, findings), (0, "OK", []))
        self.assertEqual(attempts, [1, 2])

    def test_retry_returns_failure_findings_when_scoped_api_errors_persist(self) -> None:
        """Cover retry returns failure findings when scoped api errors persist."""
        args = argparse.Namespace(branch="", pull_request="5")
        attempts: List[int] = []

        def fake_loader(current_args, auth):
            """Handle fake loader."""
            attempts.append(len(attempts) + 1)
            raise RuntimeError("HTTP Error 404")

        open_issues, quality_gate, findings = load_sonar_findings_with_retry(
            args,
            "auth",
            fetch_fn=fake_loader,
            attempts=2,
            sleep_seconds=0.0,
        )

        self.assertEqual(open_issues, 0)
        self.assertEqual(quality_gate, "UNKNOWN")
        self.assertEqual(findings, ["Sonar API request failed: HTTP Error 404"])
        self.assertEqual(attempts, [1, 2])

    def test_retry_raises_unscoped_api_errors_without_retrying(self) -> None:
        """Cover retry raises unscoped api errors without retrying."""
        args = argparse.Namespace(branch="", pull_request="")

        def fake_loader(current_args, auth):
            """Handle fake loader."""
            raise RuntimeError("provider down")

        with self.assertRaisesRegex(RuntimeError, "provider down"):
            load_sonar_findings_with_retry(
                args,
                "auth",
                fetch_fn=fake_loader,
                attempts=2,
                sleep_seconds=0.0,
            )

    def test_retry_keyword_only_guards_reject_invalid_invocations(self) -> None:
        """Cover retry keyword only guards reject invalid invocations."""
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
        """Cover retry skips unscoped queries."""
        args = argparse.Namespace(branch="", pull_request="")
        attempts: List[int] = []

        def fake_loader(current_args, auth):
            """Handle fake loader."""
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

    def test_retry_reports_pending_status_failures(self) -> None:
        """Cover retry reports pending status failures."""
        args = argparse.Namespace(branch="main", pull_request="", sha="targetsha")
        open_issues, quality_gate, findings = load_sonar_findings_with_retry(
            args,
            "auth",
            fetch_fn=lambda _args, _auth: (0, "OK", []),
            pending_fn=lambda _args, _auth: (_ for _ in ()).throw(RuntimeError("pending broke")),
            attempts=1,
            sleep_seconds=0.0,
        )

        self.assertEqual(open_issues, 0)
        self.assertEqual(quality_gate, "OK")
        self.assertEqual(findings, ["Sonar analysis status request failed: pending broke"])

    def test_retry_returns_last_scoped_result_after_retry_budget_is_exhausted(self) -> None:
        """Cover retry returns last scoped result after retry budget is exhausted."""
        args = argparse.Namespace(branch="", pull_request="5")
        attempts: List[int] = []

        def fake_loader(current_args, auth):
            """Handle fake loader."""
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

    def test_retry_reports_pending_revision_when_retry_budget_is_exhausted(self) -> None:
        """Cover retry reports pending revision when retry budget is exhausted."""
        args = argparse.Namespace(branch="main", pull_request="", sha="targetsha")

        open_issues, quality_gate, findings = load_sonar_findings_with_retry(
            args,
            "auth",
            fetch_fn=lambda _args, _auth: (0, "OK", []),
            pending_fn=lambda _args, _auth: "Sonar analysis for branch main is not available yet.",
            attempts=2,
            sleep_seconds=0.0,
        )

        self.assertEqual(open_issues, 0)
        self.assertEqual(quality_gate, "OK")
        self.assertEqual(findings, ["Sonar analysis for branch main is not available yet."])

    def test_retry_default_budget_handles_transient_none_quality_gate_for_prs(self) -> None:
        """Cover retry default budget handles transient none quality gate for prs."""
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
            """Handle fake loader."""
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
        """Cover main handles missing token success and report failures."""
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

        audit_args = Namespace(**{**success_args.__dict__, "policy_mode": "audit"})
        with patch.object(check_sonar_zero, "_parse_args", return_value=audit_args), patch.object(
            check_sonar_zero, "load_sonar_findings_with_retry", return_value=(3, "ERROR", ["Sonar reports 3 open issues (expected 0)."])
        ), patch.object(check_sonar_zero, "write_report", return_value=0) as write_report_mock:
            self.assertEqual(check_sonar_zero.main(), 0)
        self.assertEqual(write_report_mock.call_args.args[0]["status"], "pass")

    def test_parse_args_render_markdown_and_script_entrypoint(self) -> None:
        """Cover parse args render markdown and script entrypoint."""
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
