"""Scoped-path Sonar zero-gate tests."""

from __future__ import absolute_import

import os
import runpy
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from scripts.quality import check_sonar_zero


def _marker(*parts: str) -> str:
    """Build one non-secret marker for tests."""
    return "-".join(parts)


class SonarZeroScopedTests(unittest.TestCase):
    """Scoped-path Sonar zero-gate tests."""

    def _assert_sonar_request_round_trip(self, scenario: dict) -> None:
        """Assert one Sonar request sequence and query parameter shape."""
        captured_urls = []
        responses = list(scenario["responses"])

        def fake_request(url: str, auth_header: str):
            """Capture the scoped request URL and return the next fake response."""
            captured_urls.append(url)
            return responses.pop(0)

        with patch(
            "scripts.quality.check_sonar_zero._request_json", side_effect=fake_request
        ):
            self.assertEqual(
                check_sonar_zero._load_open_issues(scenario["args"], "auth"),
                scenario["expected_open_issues"],
            )
            self.assertEqual(
                check_sonar_zero._load_quality_gate(scenario["args"], "auth"),
                scenario["expected_quality_gate"],
            )
        self.assertIn(scenario["expected_query"], captured_urls[0])
        self.assertIn(scenario["expected_query"], captured_urls[1])

    def _assert_revision_lookup(self, scenario: dict) -> None:
        """Assert one scoped revision lookup and pending-message scenario."""
        with patch.object(
            check_sonar_zero, "_request_json", return_value=scenario["payload"]
        ):
            self.assertEqual(
                scenario["revision_loader"](scenario["args"], "auth"),
                scenario["expected_revision"],
            )
            self.assertEqual(
                check_sonar_zero._scoped_analysis_pending_message(
                    scenario["args"], "auth"
                ),
                scenario["expected_pending_message"],
            )

    def _assert_main_result(self, scenario: dict) -> None:
        """Exercise one Sonar main-path scenario."""
        with patch.object(
            check_sonar_zero, "_parse_args", return_value=scenario["args"]
        ), patch.object(
            check_sonar_zero,
            "write_report",
            return_value=scenario.get("write_report_result", 0),
        ) as write_report_mock, patch.object(
            check_sonar_zero,
            "load_sonar_findings_with_retry",
            return_value=scenario.get("load_result"),
            side_effect=scenario.get("load_side_effect"),
        ):
            self.assertEqual(check_sonar_zero.main(), scenario["expected_code"])
        payload = write_report_mock.call_args.args[0]
        expected_findings = scenario.get("expected_findings")
        if expected_findings is not None:
            self.assertEqual(payload["findings"], expected_findings)
        expected_status = scenario.get("expected_status")
        if expected_status is not None:
            self.assertEqual(payload["status"], expected_status)

    def test_load_helpers_collect_open_issues_and_quality_gate(self) -> None:
        """Cover scoped and branch request round trips."""
        self._assert_sonar_request_round_trip(
            {
                "args": Namespace(
                    project_key="project",
                    branch="",
                    pull_request="5",
                    policy_mode="zero",
                ),
                "responses": [
                    {"paging": {"total": 1}},
                    {"projectStatus": {"status": "ERROR"}},
                ],
                "expected_open_issues": 1,
                "expected_quality_gate": "ERROR",
                "expected_query": "pullRequest=5",
            }
        )
        self._assert_sonar_request_round_trip(
            {
                "args": Namespace(
                    project_key="project",
                    branch="main",
                    pull_request="",
                    policy_mode="zero",
                ),
                "responses": [
                    {"paging": {"total": 0}},
                    {"projectStatus": {"status": "OK"}},
                ],
                "expected_open_issues": 0,
                "expected_quality_gate": "OK",
                "expected_query": "branch=main",
            }
        )

    def test_load_helpers_build_findings_for_zero_and_ratchet_modes(self) -> None:
        """Cover findings rendering for zero and ratchet modes."""
        args = Namespace(project_key="project", branch="", pull_request="5", policy_mode="zero")
        with patch.object(
            check_sonar_zero, "_load_open_issues", return_value=2
        ), patch.object(check_sonar_zero, "_load_quality_gate", return_value="ERROR"):
            open_issues, quality_gate, findings = check_sonar_zero._load_sonar_findings(
                args, "auth"
            )
        self.assertEqual(open_issues, 2)
        self.assertEqual(quality_gate, "ERROR")
        self.assertIn("Sonar reports 2 open issues", findings[0])
        self.assertIn("Sonar quality gate status is ERROR", findings[1])

        with patch.object(
            check_sonar_zero, "_load_open_issues", return_value=0
        ), patch.object(check_sonar_zero, "_load_quality_gate", return_value="OK"):
            self.assertEqual(
                check_sonar_zero._load_sonar_findings(args, "auth"), (0, "OK", [])
            )

        ratchet_args = Namespace(
            project_key="project", branch="", pull_request="5", policy_mode="ratchet"
        )
        with patch.object(
            check_sonar_zero, "_load_open_issues", return_value=6
        ), patch.object(check_sonar_zero, "_load_quality_gate", return_value="OK"):
            self.assertEqual(
                check_sonar_zero._load_sonar_findings(ratchet_args, "auth"),
                (6, "OK", []),
            )

    def test_revision_helpers_cover_branch_scoped_paths(self) -> None:
        """Cover branch-scoped revision lookup paths."""
        args = Namespace(
            project_key="project", branch="main", pull_request="", sha="targetsha"
        )
        self._assert_revision_lookup(
            {
                "args": args,
                "payload": {"branches": [{"name": "main", "commit": {"sha": "oldsha"}}]},
                "expected_revision": "oldsha",
                "expected_pending_message": (
                    "Sonar analysis for branch main is still on oldsha "
                    "(waiting for targetsha)."
                ),
                "revision_loader": check_sonar_zero._load_branch_analysis_revision,
            }
        )
        self._assert_revision_lookup(
            {
                "args": args,
                "payload": {"branches": [{"name": "other", "commit": {"sha": "oldsha"}}]},
                "expected_revision": "",
                "expected_pending_message": "Sonar analysis for branch main is not available yet.",
                "revision_loader": check_sonar_zero._load_branch_analysis_revision,
            }
        )

    def test_revision_helpers_cover_pull_request_and_empty_scope_paths(self) -> None:
        """Cover pull-request and unscoped pending-message paths."""
        pr_args = Namespace(
            project_key="project", branch="", pull_request="5", sha="targetsha"
        )
        self._assert_revision_lookup(
            {
                "args": pr_args,
                "payload": {
                    "pullRequests": [{"key": "5", "commit": {"sha": "targetsha"}}]
                },
                "expected_revision": "targetsha",
                "expected_pending_message": None,
                "revision_loader": check_sonar_zero._load_pull_request_analysis_revision,
            }
        )
        self._assert_revision_lookup(
            {
                "args": pr_args,
                "payload": {
                    "pullRequests": [{"key": "other", "commit": {"sha": "oldsha"}}]
                },
                "expected_revision": "",
                "expected_pending_message": "Sonar analysis for pull request 5 is not available yet.",
                "revision_loader": check_sonar_zero._load_pull_request_analysis_revision,
            }
        )
        self.assertIsNone(
            check_sonar_zero._scoped_analysis_pending_message(
                Namespace(project_key="project", branch="", pull_request="", sha=""),
                "auth",
            )
        )

    def test_main_requires_sonar_token(self) -> None:
        """Cover the missing-token main path."""
        args = Namespace(
            project_key="Prekzursil_quality-zero-platform",
            token=str(),
            branch="",
            pull_request="5",
            out_json="sonar-zero/sonar.json",
            out_md="sonar-zero/sonar.md",
        )
        with patch.dict("os.environ", {}, clear=True):
            self._assert_main_result(
                {
                    "args": args,
                    "expected_code": 1,
                    "expected_findings": ["SONAR_TOKEN is missing."],
                }
            )

    def test_main_handles_success_and_provider_failures(self) -> None:
        """Cover successful and failing provider responses."""
        args = Namespace(
            project_key="Prekzursil_quality-zero-platform",
            token=_marker("provided", "value"),
            branch="",
            pull_request="5",
            out_json="sonar-zero/sonar.json",
            out_md="sonar-zero/sonar.md",
        )
        self._assert_main_result(
            {
                "args": args,
                "load_result": (0, "OK", []),
                "expected_code": 0,
                "expected_status": "pass",
            }
        )
        self._assert_main_result(
            {
                "args": args,
                "load_side_effect": RuntimeError("provider timeout"),
                "expected_code": 1,
                "expected_findings": ["Sonar API request failed: provider timeout"],
            }
        )

    def test_main_propagates_write_report_failures_and_audit_mode(self) -> None:
        """Cover write-report failure and audit-mode success paths."""
        args = Namespace(
            project_key="Prekzursil_quality-zero-platform",
            token="provided-token",
            branch="",
            pull_request="5",
            out_json="sonar-zero/sonar.json",
            out_md="sonar-zero/sonar.md",
        )
        self._assert_main_result(
            {
                "args": args,
                "load_result": (0, "OK", []),
                "write_report_result": 4,
                "expected_code": 4,
                "expected_status": "pass",
            }
        )
        audit_args = Namespace(**{**args.__dict__, "policy_mode": "audit"})
        self._assert_main_result(
            {
                "args": audit_args,
                "load_result": (
                    3,
                    "ERROR",
                    ["Sonar reports 3 open issues (expected 0)."],
                ),
                "expected_code": 0,
                "expected_status": "pass",
            }
        )

    def test_parse_args_render_markdown_and_script_entrypoint(self) -> None:
        """Cover parse args, markdown rendering, and the direct script entrypoint."""
        with patch.object(
            sys, "argv", ["check_sonar_zero.py", "--project-key", "project"]
        ):
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
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {}, clear=True
        ), patch.object(
            sys,
            "argv",
            [str(script_path), "--project-key", "Prekzursil_quality-zero-platform"],
        ), patch.object(
            sys, "path", trimmed_sys_path[:]
        ):
            cwd = Path(tmp)
            previous = Path.cwd()
            os.chdir(cwd)
            try:
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(script_path), run_name="__main__")
            finally:
                os.chdir(previous)
        self.assertEqual(result.exception.code, 1)
