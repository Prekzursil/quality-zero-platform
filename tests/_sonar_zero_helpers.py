"""Shared helpers for ``test_check_sonar_zero*`` test files.

The two test files (``test_check_sonar_zero.py`` for happy-path scenarios and
``test_check_sonar_zero_edge.py`` for retry/entrypoint edge cases) used to
duplicate ~60 lines of fixture code (mocked ``_request_json``, scenario
runners, ``main()`` mock harness). qlty's smells gate previously flagged this
as duplication; centralising them here keeps both files thin while preserving
the file-split that limits each test module to ~400 lines.
"""
from __future__ import absolute_import

from typing import List
from unittest.mock import patch

from scripts.quality import check_sonar_zero


def raise_runtime_error(message: str) -> None:
    """Raise a single ``RuntimeError`` -- used as a callback test side-effect."""
    raise RuntimeError(message)


class SonarZeroHelpersMixin:
    """Mixin for ``unittest.TestCase`` subclasses that exercise check_sonar_zero."""

    def _assert_sonar_request_round_trip(self, scenario: dict) -> None:
        """Assert one Sonar request sequence and query parameter shape."""
        captured_urls: List[str] = []
        responses = list(scenario["responses"])

        def fake_request(url: str, auth_header: str):
            """Capture the URL and pop the next canned response."""
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
