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


def _raise_runtime_error(message: str) -> None:
    """Raise one runtime error for callback tests."""
    raise RuntimeError(message)


class SonarZeroTests(unittest.TestCase):
    """Sonar Zero Tests."""

    def _assert_sonar_request_round_trip(self, scenario: dict) -> None:
        """Assert one Sonar request sequence and query parameter shape."""
        captured_urls: List[str] = []
        responses = list(scenario["responses"])

        def fake_request(url: str, auth_header: str):
            """Handle fake request."""
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

    def test_auth_header_and_query_helpers_cover_scoped_inputs(self) -> None:
        """Cover auth header and query helpers cover scoped inputs."""
        auth_header = check_sonar_zero._auth_header("token")
        self.assertTrue(auth_header.startswith("Basic "))
        self.assertEqual(
            check_sonar_zero._build_sonar_query(
                "project", branch="main", pull_request="5"
            ),
            {"projectKey": "project", "branch": "main", "pullRequest": "5"},
        )

    def test_request_json_rejects_non_dict_payloads(self) -> None:
        """Cover request json rejects non dict payloads."""
        with (
            patch(
                "scripts.quality.check_sonar_zero.load_json_https",
                return_value=(["invalid"], {}),
            ),
            self.assertRaisesRegex(
                RuntimeError, "Unexpected SonarCloud API response payload"
            ),
        ):
            check_sonar_zero._request_json(
                "https://sonarcloud.io/api/issues/search", "auth"
            )
        with patch(
            "scripts.quality.check_sonar_zero.load_json_https",
            return_value=({"paging": {"total": 0}}, {}),
        ):
            self.assertEqual(
                check_sonar_zero._request_json(
                    "https://sonarcloud.io/api/issues/search", "auth"
                ),
                {"paging": {"total": 0}},
            )

    def test_load_helpers_collect_open_issues_quality_gate_and_findings(self) -> None:
        """Cover load helpers collect open issues quality gate and findings."""
        args = Namespace(
            project_key="project", branch="", pull_request="5", policy_mode="zero"
        )
        branch_args = Namespace(
            project_key="project", branch="main", pull_request="", policy_mode="zero"
        )
        self._assert_sonar_request_round_trip(
            {
                "args": args,
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
                "args": branch_args,
                "responses": [
                    {"paging": {"total": 0}},
                    {"projectStatus": {"status": "OK"}},
                ],
                "expected_open_issues": 0,
                "expected_quality_gate": "OK",
                "expected_query": "branch=main",
            }
        )

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

    def test_revision_helpers_and_pending_message_cover_scoped_paths(self) -> None:
        """Cover revision helpers and pending message cover scoped paths."""
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

