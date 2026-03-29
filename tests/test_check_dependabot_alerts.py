"""Test check dependabot alerts."""

from __future__ import absolute_import

import unittest
from unittest.mock import patch

from scripts.quality import check_dependabot_alerts


class DependabotAlertTests(unittest.TestCase):
    """Dependabot Alert Tests."""

    def test_filter_alerts_obeys_policy_threshold(self) -> None:
        """Cover filter alerts obeys policy threshold."""
        alerts = [
            {"security_vulnerability": {"severity": "critical"}},
            {"security_vulnerability": {"severity": "high"}},
            {"security_vulnerability": {"severity": "moderate"}},
        ]

        self.assertEqual(len(check_dependabot_alerts.filter_alerts(alerts, policy="zero_critical")), 1)
        self.assertEqual(len(check_dependabot_alerts.filter_alerts(alerts, policy="zero_high")), 2)
        self.assertEqual(len(check_dependabot_alerts.filter_alerts(alerts, policy="zero_any")), 3)

    def test_request_alerts_follows_github_pagination(self) -> None:
        """Cover request alerts follows github pagination."""
        responses = [
            ([{"number": 1}], {"link": '<https://api.github.com/repos/Prekzursil/quality-zero-platform/dependabot/alerts?page=2>; rel="next"'}),
            ([{"number": 2}], {}),
        ]

        with patch.object(check_dependabot_alerts, "load_json_https", side_effect=responses) as load_json_https_mock:
            payload = check_dependabot_alerts._request_alerts(
                "Prekzursil/quality-zero-platform",
                "token",
                scope="runtime",
            )

        self.assertEqual([item["number"] for item in payload], [1, 2])
        self.assertEqual(load_json_https_mock.call_count, 2)

    def test_main_handles_missing_token_and_open_alerts(self) -> None:
        """Cover main handles missing token and open alerts."""
        args = check_dependabot_alerts.argparse.Namespace(
            repo="Prekzursil/quality-zero-platform",
            token=None,
            policy="zero_critical",
            scope="runtime",
            out_json="deps-zero/deps.json",
            out_md="deps-zero/deps.md",
        )
        with patch.dict("os.environ", {}, clear=True), patch.object(check_dependabot_alerts, "_parse_args", return_value=args), patch.object(
            check_dependabot_alerts, "write_report", return_value=0
        ) as write_report_mock:
            self.assertEqual(check_dependabot_alerts.main(), 1)
        self.assertIn("GITHUB_TOKEN or GH_TOKEN is required.", write_report_mock.call_args.args[0]["findings"])

        live_args = check_dependabot_alerts.argparse.Namespace(**{**args.__dict__, "token": "token"})
        with patch.object(
            check_dependabot_alerts,
            "_request_alerts",
            return_value=[{"security_vulnerability": {"severity": "critical"}}],
        ), patch.object(
            check_dependabot_alerts, "_parse_args", return_value=live_args
        ), patch.object(check_dependabot_alerts, "write_report", return_value=0) as write_report_mock:
            self.assertEqual(check_dependabot_alerts.main(), 1)
        self.assertEqual(write_report_mock.call_args.args[0]["open_alerts"], 1)

        with patch.object(
            check_dependabot_alerts,
            "_request_alerts",
            return_value=[],
        ), patch.object(
            check_dependabot_alerts, "_parse_args", return_value=live_args
        ), patch.object(check_dependabot_alerts, "write_report", return_value=4):
            self.assertEqual(check_dependabot_alerts.main(), 4)
