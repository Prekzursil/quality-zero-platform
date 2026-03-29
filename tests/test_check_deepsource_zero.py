"""Test check deepsource zero."""

from __future__ import absolute_import

import sys
import unittest
from argparse import Namespace
from contextlib import ExitStack
from unittest.mock import patch

from scripts.quality import check_deepsource_zero
from scripts.quality.deepsource_html import human_count_to_int
from tests.script_entrypoint_support import (
    assert_in_process_entrypoint_failure,
    assert_main_reports_provider_failure,
    run_script_entrypoint_failure,
)


class DeepSourceVisibleZeroTests(unittest.TestCase):
    """Deep Source Visible Zero Tests."""

    @staticmethod
    def _status_poll_token() -> str:
        """Return a non-literal token string for status polling tests."""
        return "-".join(["status", "handle"])

    def _assert_issue_link_variants(self) -> None:
        """Assert the supported DeepSource issue-link extraction variants."""
        self.assertEqual(
            check_deepsource_zero.extract_issue_links(
                '<a href="/gh/Prekzursil/event-link/dashboard">ignore</a>'
                '<a href="/gh/Prekzursil/event-link/issue/JS-0125/'
                'occurrences?listindex=0">keep</a>'
            ),
            ["/gh/Prekzursil/event-link/issue/JS-0125/occurrences?listindex=0"],
        )
        self.assertEqual(
            check_deepsource_zero.extract_issue_links(
                '<a href="/gh/Prekzursil/event-link/issue/JS-0125/'
                "occurrences?listindex=0>broken</a>"
            ),
            [],
        )

    def _assert_issue_count_variants(self) -> None:
        """Assert the supported DeepSource visible-issue count variants."""
        self.assertEqual(
            check_deepsource_zero.extract_visible_issue_count(
                '<span class="flex-1">All issues</span>'
                '<div class="rounded-3px">854</div>'
            ),
            854,
        )
        self.assertIsNone(
            check_deepsource_zero.extract_visible_issue_count(
                '<span class="flex-1">All issues</span>'
                '<div class="rounded-3px">bogus</div>'
            )
        )
        self.assertEqual(
            check_deepsource_zero.extract_visible_issue_count(
                '"all",854,"recommended"'
            ),
            854,
        )
        self.assertEqual(human_count_to_int("854"), 854)
        self.assertIsNone(human_count_to_int(""))
        self.assertIsNone(human_count_to_int("bogus"))

    def _assert_status_context_filter(self) -> None:
        """Assert that only matching DeepSource status contexts are returned."""
        statuses = check_deepsource_zero._status_contexts(
            {
                "statuses": [
                    {"context": "DeepSource: Python", "state": "success"},
                    {"context": "DeepSource: JavaScript", "state": "pending"},
                    {"context": "DeepScan", "state": "success"},
                    {"context": "", "state": "failure"},
                    "invalid",
                ]
            },
            "DeepSource",
        )
        self.assertEqual(
            [item["context"] for item in statuses],
            ["DeepSource: Python", "DeepSource: JavaScript"],
        )

    @staticmethod
    def _main_args() -> Namespace:
        """Return standard CLI arguments for DeepSource main-path tests."""
        return Namespace(
            repo="Prekzursil/event-link",
            sha="abc123",
            issues_url=(
                "https://app.deepsource.com/gh/Prekzursil/"
                "event-link/issues?category=all&page=1"
            ),
            status_prefix="DeepSource",
            timeout_seconds=1,
            poll_seconds=0,
            out_json="deepsource-visible-zero/deepsource.json",
            out_md="deepsource-visible-zero/deepsource.md",
        )

    def _assert_main_result(self, scenario: dict) -> None:
        """Exercise one DeepSource main-path scenario."""
        env = scenario.get("env", {})
        with patch.dict("os.environ", env, clear=not env), patch.object(
            check_deepsource_zero, "_parse_args", return_value=self._main_args()
        ), patch.object(
            check_deepsource_zero,
            "write_report",
            return_value=scenario.get("write_report_result", 0),
        ) as write_report_mock, ExitStack() as stack:
            wait_result = scenario.get("wait_result")
            if wait_result is not None:
                stack.enter_context(
                    patch.object(
                        check_deepsource_zero,
                        "_wait_for_status_contexts",
                        return_value=wait_result,
                    )
                )
            evaluate_mock = stack.enter_context(
                patch.object(
                    check_deepsource_zero,
                    "_evaluate_visible_issues",
                    return_value=scenario.get("visible_result"),
                )
            )
            self.assertEqual(check_deepsource_zero.main(), scenario["expected_code"])
        if scenario.get("expect_visible_called", True):
            evaluate_mock.assert_called_once()
        else:
            evaluate_mock.assert_not_called()
        self.assertEqual(
            write_report_mock.call_args.args[0]["status"], scenario["expected_status"]
        )

    def _assert_visible_issue_evaluation(
        self,
        html: str,
        expected_open_issues: int,
        expected_findings,
    ) -> None:
        """Assert one visible-issue evaluation scenario."""
        with patch.object(check_deepsource_zero, "_request_html", return_value=html):
            open_issues, findings = check_deepsource_zero._evaluate_visible_issues(
                "https://app.deepsource.com/gh/Prekzursil/event-link/issues"
            )
        self.assertEqual(open_issues, expected_open_issues)
        self.assertEqual(findings, expected_findings)

    def test_extractors_cover_sidebar_counts_issue_links_and_status_filters(
        self,
    ) -> None:
        """Cover extractors cover sidebar counts issue links and status filters."""
        html = """
        <span>All issues</span><div>1.9k</div>
        <a href="/gh/Prekzursil/event-link/issue/JS-0125/occurrences?listindex=0">one</a>
        <a href="/gh/Prekzursil/event-link/issue/JS-0125/occurrences?listindex=0">two</a>
        """
        self.assertEqual(check_deepsource_zero.extract_visible_issue_count(html), 1900)
        self.assertEqual(
            check_deepsource_zero.extract_issue_links(html),
            ["/gh/Prekzursil/event-link/issue/JS-0125/occurrences?listindex=0"],
        )
        self._assert_issue_link_variants()
        self._assert_issue_count_variants()
        self._assert_status_context_filter()

    def test_repo_sha_and_issue_url_resolution_follow_env_and_defaults(self) -> None:
        """Cover repo sha and issue url resolution follow env and defaults."""
        args = Namespace(repo="", sha="", issues_url="")
        with patch.dict(
            "os.environ",
            {"REPO_SLUG": "Prekzursil/quality-zero-platform", "TARGET_SHA": "abc123"},
            clear=False,
        ):
            self.assertEqual(
                check_deepsource_zero._github_repo(args),
                "Prekzursil/quality-zero-platform",
            )
            self.assertEqual(check_deepsource_zero._github_sha(args), "abc123")
            self.assertEqual(
                check_deepsource_zero._issues_url(args),
                (
                    "https://app.deepsource.com/gh/Prekzursil/"
                    "quality-zero-platform/issues?category=all&page=1"
                ),
            )
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                check_deepsource_zero._issues_url(Namespace(repo="", sha="", issues_url="")),
                "",
            )

    def test_visible_zero_inputs_falls_back_to_empty_issue_url_on_resolution_error(
        self,
    ) -> None:
        """Cover visible-zero inputs when the issues URL cannot be resolved."""
        args = Namespace(repo="Prekzursil/quality-zero-platform", sha="abc123")
        with patch.dict("os.environ", {"GH_TOKEN": "token"}, clear=True), patch.object(
            check_deepsource_zero,
            "_issues_url",
            side_effect=ValueError("missing issues url"),
        ):
            inputs = check_deepsource_zero._visible_zero_inputs(args)
        self.assertEqual(inputs.token, "token")
        self.assertEqual(inputs.repo, "Prekzursil/quality-zero-platform")
        self.assertEqual(inputs.sha, "abc123")
        self.assertEqual(inputs.issues_url, "")

    def test_validate_inputs_and_status_findings_cover_missing_and_failure_paths(
        self,
    ) -> None:
        """Cover validate inputs and status findings cover missing and failure paths."""
        self.assertEqual(
            check_deepsource_zero._validate_inputs("", "", "", ""),
            [
                "GITHUB_TOKEN or GH_TOKEN is required.",
                "REPO_SLUG or GITHUB_REPOSITORY is required.",
                "TARGET_SHA or GITHUB_SHA is required.",
                "DeepSource issues URL could not be resolved.",
            ],
        )
        self.assertEqual(
            check_deepsource_zero._status_findings([], "DeepSource"),
            ["DeepSource GitHub status contexts are missing."],
        )
        self.assertEqual(
            check_deepsource_zero._status_findings(
                [{"context": "DeepSource: Python", "state": "failure"}],
                "DeepSource",
            ),
            ["DeepSource: Python GitHub status is failure (expected success)."],
        )
        self.assertEqual(
            check_deepsource_zero._status_findings(
                [{"context": "DeepSource: JavaScript", "state": "pending"}],
                "DeepSource",
            ),
            ["DeepSource: JavaScript GitHub status is pending (expected success)."],
        )
        self.assertEqual(
            check_deepsource_zero._status_findings(
                [{"context": "DeepSource: JavaScript", "state": ""}],
                "DeepSource",
            ),
            ["DeepSource: JavaScript GitHub status is unknown (expected success)."],
        )

    def test_github_status_payload_and_request_html_guard_payload_shape(self) -> None:
        """Cover github status payload and request html guard payload shape."""
        with patch(
            "scripts.quality.common.load_json_https",
            return_value=(["invalid"], {}),
        ), self.assertRaisesRegex(
            RuntimeError,
            "Unexpected GitHub status response payload",
        ):
            check_deepsource_zero._github_status_payload(
                "Prekzursil/quality-zero-platform",
                "abc123",
                "token",
            )
        with patch(
            "scripts.quality.common.load_json_https",
            return_value=({"statuses": []}, {}),
        ):
            self.assertEqual(
                check_deepsource_zero._github_status_payload(
                    "Prekzursil/quality-zero-platform",
                    "abc123",
                    "token",
                ),
                {"statuses": []},
            )
        with patch(
            "scripts.quality.check_deepsource_zero.load_bytes_https",
            return_value=(b"<html>ok</html>", {}),
        ):
            self.assertEqual(
                check_deepsource_zero._request_html(
                    "https://app.deepsource.com/gh/Prekzursil/event-link/issues"
                ),
                "<html>ok</html>",
            )

    def test_wait_for_status_contexts_polls_until_non_pending_contexts_arrive(
        self,
    ) -> None:
        """Cover wait for status contexts polls until non pending contexts arrive."""
        payloads = [
            {"statuses": [{"context": "DeepSource: Python", "state": "pending"}]},
            {"statuses": [{"context": "DeepSource: Python", "state": "success"}]},
        ]
        with patch.object(
            check_deepsource_zero,
            "_github_status_payload",
            side_effect=payloads,
        ), patch("scripts.quality.check_deepsource_zero.time.sleep") as sleep_mock:
            token_value = "-".join(["status", "handle"])
            statuses, findings = check_deepsource_zero._wait_for_status_contexts(
                check_deepsource_zero.StatusPollRequest(
                    repo="Prekzursil/quality-zero-platform",
                    sha="abc123",
                    token=token_value,
                    prefix="DeepSource",
                    timeout_seconds=2,
                    poll_seconds=0,
                )
            )
        self.assertEqual([item["state"] for item in statuses], ["success"])
        self.assertEqual(findings, [])
        sleep_mock.assert_called_once()

    def test_wait_for_status_contexts_times_out_and_preserves_pending_findings(
        self,
    ) -> None:
        """Cover wait for status contexts times out and preserves pending findings."""
        with patch.object(
            check_deepsource_zero,
            "_github_status_payload",
            return_value={
                "statuses": [{"context": "DeepSource: Python", "state": "pending"}]
            },
        ), patch(
            "scripts.quality.check_deepsource_zero.time.time",
            side_effect=[0, 0, 2],
        ), patch(
            "scripts.quality.check_deepsource_zero.time.sleep"
        ) as sleep_mock:
            statuses, findings = check_deepsource_zero._wait_for_status_contexts(
                check_deepsource_zero.StatusPollRequest(
                    repo="Prekzursil/quality-zero-platform",
                    sha="abc123",
                    token=self._status_poll_token(),
                    prefix="DeepSource",
                    timeout_seconds=1,
                    poll_seconds=0,
                )
            )
        self.assertEqual([item["state"] for item in statuses], ["pending"])
        self.assertEqual(
            findings,
            ["DeepSource: Python GitHub status is pending (expected success)."],
        )
        sleep_mock.assert_called_once()

    def test_evaluate_visible_issues_handles_zero_nonzero_and_unparseable_pages(
        self,
    ) -> None:
        """Cover evaluate visible issues handles zero nonzero and unparseable pages."""
        self._assert_visible_issue_evaluation("<span>All issues</span><div>0</div>", 0, [])
        self._assert_visible_issue_evaluation(
            "<span>All issues</span><div>854</div>",
            854,
            ["DeepSource shows 854 visible issues on the default branch (expected 0)."],
        )
        self._assert_visible_issue_evaluation(
            '<a href="/gh/Prekzursil/event-link/issue/PYL-W0108/occurrences?listindex=0">x</a>',
            1,
            ["DeepSource shows 1 visible issues on the default branch (expected 0)."],
        )
        self._assert_visible_issue_evaluation(
            "<span>All issues</span><div>0</div>"
            '<a href="/gh/Prekzursil/event-link/issue/PYL-W0108/occurrences?listindex=0">x</a>',
            0,
            [
                "DeepSource returned issue cards even though the total issue "
                "count resolved to 0."
            ],
        )

    def test_main_handles_success_missing_inputs_and_provider_errors(self) -> None:
        """Cover main handles success missing inputs and provider errors."""
        self._assert_main_result(
            {
                "env": {},
                "visible_result": (0, []),
                "expected_code": 1,
                "expected_status": "fail",
                "expect_visible_called": False,
            }
        )
        self._assert_main_result(
            {
                "env": {"GITHUB_TOKEN": "token"},
                "wait_result": (
                    [{"context": "DeepSource: Python", "state": "success"}],
                    [],
                ),
                "visible_result": (0, []),
                "expected_code": 0,
                "expected_status": "pass",
            }
        )
        self._assert_main_result(
            {
                "env": {"GITHUB_TOKEN": "token"},
                "wait_result": (
                    [{"context": "DeepSource: Python", "state": "failure"}],
                    ["DeepSource: Python GitHub status is failure (expected success)."],
                ),
                "visible_result": (0, []),
                "expected_code": 1,
                "expected_status": "fail",
                "expect_visible_called": False,
            }
        )
        self._assert_main_result(
            {
                "env": {"GITHUB_TOKEN": "token"},
                "wait_result": (
                    [{"context": "DeepSource: Python", "state": "success"}],
                    [],
                ),
                "visible_result": (0, []),
                "write_report_result": 7,
                "expected_code": 7,
                "expected_status": "pass",
            }
        )

        assert_main_reports_provider_failure(
            self,
            check_deepsource_zero,
            {
                "env": {"GITHUB_TOKEN": "token"},
                "args": self._main_args(),
                "operation_name": "_wait_for_status_contexts",
                "failure_message": "provider timeout",
                "expected_finding": "DeepSource request failed: provider timeout",
            },
        )

    def test_parse_args_render_markdown_and_script_entrypoint(self) -> None:
        """Cover parse args render markdown and script entrypoint."""
        with patch.object(sys, "argv", ["check_deepsource_zero.py"]):
            args = check_deepsource_zero._parse_args()
        self.assertEqual(args.status_prefix, "DeepSource")

        markdown = check_deepsource_zero._render_md(
            {
                "status": "pass",
                "open_issues": 0,
                "issues_url": "",
                "status_contexts": [],
                "timestamp_utc": "2026-03-29T00:00:00+00:00",
                "findings": [],
            }
        )
        self.assertIn("DeepSource Visible Zero Gate", markdown)
        self.assertIn("`n/a`", markdown)
        self.assertIn("- None", markdown)
        self.assertEqual(
            check_deepsource_zero._status_target_urls(
                [
                    {"target_url": "https://example.test/a"},
                    {"target_url": "https://example.test/a"},
                    {"target_url": "https://example.test/b"},
                    {"target_url": ""},
                ]
            ),
            ["https://example.test/a", "https://example.test/b"],
        )
        self.assertTrue(
            check_deepsource_zero._statuses_are_ready(
                [{"context": "DeepSource: Python", "state": "success"}]
            )
        )
        self.assertFalse(
            check_deepsource_zero._statuses_are_ready(
                [{"context": "DeepSource: Python", "state": "pending"}]
            )
        )

        self.assertEqual(
            run_script_entrypoint_failure("scripts/quality/check_deepsource_zero.py"),
            1,
        )
        assert_in_process_entrypoint_failure(
            self, "scripts/quality/check_deepsource_zero.py"
        )
