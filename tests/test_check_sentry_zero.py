from __future__ import absolute_import

import unittest
from email.message import Message
from urllib.error import HTTPError
from unittest.mock import patch

from scripts.quality import check_sentry_zero as sentry_module


class SentryZeroTests(unittest.TestCase):
    """Exercise the Sentry zero gate's project evaluation paths."""

    def test_collect_project_results_marks_missing_projects_as_not_found(self) -> None:
        """Treat missing Sentry projects as a non-blocking configuration gap."""
        with patch.object(
            sentry_module,
            "_request_json",
            side_effect=HTTPError("https://sentry.io/api/0/projects/prekzursil/event-link/issues/", 404, "Not Found", hdrs=Message(), fp=None),
        ):
            results, findings = sentry_module._collect_project_results("prekzursil", ["event-link"], "token-123")

        self.assertEqual(results, [{"project": "event-link", "unresolved": 0, "state": "not_found"}])
        self.assertEqual(findings, [])

    def test_collect_project_results_marks_present_projects_as_ok(self) -> None:
        """Keep successful Sentry project probes visible in the markdown output."""
        with patch.object(
            sentry_module,
            "_request_json",
            return_value=([], {"x-hits": "0"}),
        ):
            results, findings = sentry_module._collect_project_results("prekzursil", ["quality-zero-platform"], "token-123")

        self.assertEqual(results, [{"project": "quality-zero-platform", "unresolved": 0, "state": "ok"}])
        self.assertEqual(findings, [])

    def test_render_md_includes_project_state_suffixes(self) -> None:
        """Show non-default project states in the human-readable report."""
        markdown = sentry_module._render_md(
            {
                "status": "pass",
                "org": "prekzursil",
                "timestamp_utc": "2026-03-28T00:00:00+00:00",
                "projects": [
                    {"project": "quality-zero-platform", "unresolved": 0, "state": "ok"},
                    {"project": "event-link", "unresolved": 0, "state": "not_found"},
                ],
                "findings": [],
            }
        )

        self.assertIn("`quality-zero-platform` unresolved=`0`", markdown)
        self.assertIn("`event-link` unresolved=`0` state=`not_found`", markdown)
