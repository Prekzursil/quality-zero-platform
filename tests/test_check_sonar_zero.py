"""Test check sonar zero."""

from __future__ import absolute_import

import argparse
import unittest
from typing import List
from unittest.mock import patch

from scripts.quality import check_sonar_zero
from scripts.quality.check_sonar_zero import load_sonar_findings_with_retry


def _raise_runtime_error(message: str) -> None:
    """Raise one runtime error for callback tests."""
    raise RuntimeError(message)


class SonarZeroTests(unittest.TestCase):
    """Sonar Zero Tests."""

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
            (
                "Sonar analysis for branch main is still on oldsha "
                "(waiting for targetsha)."
            ),
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

    def test_retry_returns_failure_findings_when_scoped_api_errors_persist(
        self,
    ) -> None:
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

        with self.assertRaisesRegex(
            TypeError, "expects argparse namespace and auth header"
        ):
            load_sonar_findings_with_retry(args)

        with self.assertRaisesRegex(
            TypeError, "Unexpected load_sonar_findings_with_retry parameters: extra"
        ):
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

        self.assertEqual(
            (open_issues, quality_gate, findings),
            (3, "ERROR", ["Sonar reports 3 open issues (expected 0)."]),
        )
        self.assertEqual(attempts, [1])

    def test_retry_reports_pending_status_failures(self) -> None:
        """Cover retry reports pending status failures."""
        args = argparse.Namespace(branch="main", pull_request="", sha="targetsha")
        open_issues, quality_gate, findings = load_sonar_findings_with_retry(
            args,
            "auth",
            fetch_fn=lambda _args, _auth: (0, "OK", []),
            pending_fn=lambda _args, _auth: _raise_runtime_error("pending broke"),
            attempts=1,
            sleep_seconds=0.0,
        )

        self.assertEqual(open_issues, 0)
        self.assertEqual(quality_gate, "OK")
        self.assertEqual(
            findings, ["Sonar analysis status request failed: pending broke"]
        )

    def test_retry_returns_last_scoped_result_after_retry_budget_is_exhausted(
        self,
    ) -> None:
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

        self.assertEqual(
            (open_issues, quality_gate, findings),
            (1, "ERROR", ["Sonar reports 1 open issues (expected 0)."]),
        )
        self.assertEqual(attempts, [1, 2])

    def test_retry_reports_pending_revision_when_retry_budget_is_exhausted(
        self,
    ) -> None:
        """Cover retry reports pending revision when retry budget is exhausted."""
        args = argparse.Namespace(branch="main", pull_request="", sha="targetsha")

        open_issues, quality_gate, findings = load_sonar_findings_with_retry(
            args,
            "auth",
            fetch_fn=lambda _args, _auth: (0, "OK", []),
            pending_fn=lambda _args, _auth: (
                "Sonar analysis for branch main is not available yet."
            ),
            attempts=2,
            sleep_seconds=0.0,
        )

        self.assertEqual(open_issues, 0)
        self.assertEqual(quality_gate, "OK")
        self.assertEqual(
            findings, ["Sonar analysis for branch main is not available yet."]
        )

    def test_retry_default_budget_handles_transient_none_quality_gate_for_prs(
        self,
    ) -> None:
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
