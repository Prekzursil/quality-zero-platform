from __future__ import absolute_import

import os
import runpy
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from scripts.quality import check_deepsource_zero


class DeepSourceVisibleZeroTests(unittest.TestCase):
    def test_extractors_cover_sidebar_counts_issue_links_and_status_filters(self) -> None:
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
        self.assertEqual(check_deepsource_zero._human_count_to_int("854"), 854)
        self.assertIsNone(check_deepsource_zero._human_count_to_int("bogus"))

        statuses = check_deepsource_zero._status_contexts(
            {
                "statuses": [
                    {"context": "DeepSource: Python", "state": "success"},
                    {"context": "DeepSource: JavaScript", "state": "pending"},
                    {"context": "DeepScan", "state": "success"},
                ]
            },
            "DeepSource",
        )
        self.assertEqual([item["context"] for item in statuses], ["DeepSource: Python", "DeepSource: JavaScript"])

    def test_repo_sha_and_issue_url_resolution_follow_env_and_defaults(self) -> None:
        args = Namespace(repo="", sha="", issues_url="")
        with patch.dict(
            "os.environ",
            {"REPO_SLUG": "Prekzursil/quality-zero-platform", "TARGET_SHA": "abc123"},
            clear=False,
        ):
            self.assertEqual(check_deepsource_zero._github_repo(args), "Prekzursil/quality-zero-platform")
            self.assertEqual(check_deepsource_zero._github_sha(args), "abc123")
            self.assertEqual(
                check_deepsource_zero._issues_url(args),
                "https://app.deepsource.com/gh/Prekzursil/quality-zero-platform/issues?category=all&page=1",
            )

    def test_validate_inputs_and_status_findings_cover_missing_and_failure_paths(self) -> None:
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

    def test_github_status_payload_and_request_html_guard_payload_shape(self) -> None:
        with patch("scripts.quality.check_deepsource_zero.load_json_https", return_value=(["invalid"], {})):
            with self.assertRaisesRegex(RuntimeError, "Unexpected GitHub status response payload"):
                check_deepsource_zero._github_status_payload("Prekzursil/quality-zero-platform", "abc123", "token")
        with patch("scripts.quality.check_deepsource_zero.load_json_https", return_value=({"statuses": []}, {})):
            self.assertEqual(
                check_deepsource_zero._github_status_payload("Prekzursil/quality-zero-platform", "abc123", "token"),
                {"statuses": []},
            )
        with patch("scripts.quality.check_deepsource_zero.load_bytes_https", return_value=(b"<html>ok</html>", {})):
            self.assertEqual(check_deepsource_zero._request_html("https://app.deepsource.com/gh/Prekzursil/event-link/issues"), "<html>ok</html>")

    def test_wait_for_status_contexts_polls_until_non_pending_contexts_arrive(self) -> None:
        payloads = [
            {"statuses": [{"context": "DeepSource: Python", "state": "pending"}]},
            {"statuses": [{"context": "DeepSource: Python", "state": "success"}]},
        ]
        with patch.object(check_deepsource_zero, "_github_status_payload", side_effect=payloads), patch(
            "scripts.quality.check_deepsource_zero.time.sleep"
        ) as sleep_mock:
            statuses, findings = check_deepsource_zero._wait_for_status_contexts(
                repo="Prekzursil/quality-zero-platform",
                sha="abc123",
                token="token",
                prefix="DeepSource",
                timeout_seconds=2,
                poll_seconds=0,
            )
        self.assertEqual([item["state"] for item in statuses], ["success"])
        self.assertEqual(findings, [])
        sleep_mock.assert_called_once()

    def test_evaluate_visible_issues_handles_zero_nonzero_and_unparseable_pages(self) -> None:
        with patch.object(
            check_deepsource_zero,
            "_request_html",
            return_value='<span>All issues</span><div>0</div>',
        ):
            self.assertEqual(
                check_deepsource_zero._evaluate_visible_issues("https://app.deepsource.com/gh/Prekzursil/event-link/issues"),
                (0, []),
            )

        with patch.object(
            check_deepsource_zero,
            "_request_html",
            return_value='<span>All issues</span><div>854</div>',
        ):
            open_issues, findings = check_deepsource_zero._evaluate_visible_issues(
                "https://app.deepsource.com/gh/Prekzursil/quality-zero-platform/issues",
            )
        self.assertEqual(open_issues, 854)
        self.assertEqual(findings, ["DeepSource shows 854 visible issues on the default branch (expected 0)."])

        with patch.object(
            check_deepsource_zero,
            "_request_html",
            return_value='<a href="/gh/Prekzursil/event-link/issue/PYL-W0108/occurrences?listindex=0">x</a>',
        ):
            open_issues, findings = check_deepsource_zero._evaluate_visible_issues(
                "https://app.deepsource.com/gh/Prekzursil/event-link/issues",
            )
        self.assertEqual(open_issues, 1)
        self.assertEqual(findings, ["DeepSource shows 1 visible issues on the default branch (expected 0)."])

    def test_main_handles_success_missing_inputs_and_provider_errors(self) -> None:
        args = Namespace(
            repo="Prekzursil/event-link",
            sha="abc123",
            issues_url="https://app.deepsource.com/gh/Prekzursil/event-link/issues?category=all&page=1",
            status_prefix="DeepSource",
            timeout_seconds=1,
            poll_seconds=0,
            out_json="deepsource-visible-zero/deepsource.json",
            out_md="deepsource-visible-zero/deepsource.md",
        )
        with patch.dict("os.environ", {}, clear=True), patch.object(
            check_deepsource_zero,
            "_parse_args",
            return_value=args,
        ), patch.object(check_deepsource_zero, "write_report", return_value=0) as write_report_mock:
            self.assertEqual(check_deepsource_zero.main(), 1)
        self.assertEqual(write_report_mock.call_args.args[0]["status"], "fail")

        with patch.dict("os.environ", {"GITHUB_TOKEN": "token"}, clear=False), patch.object(
            check_deepsource_zero,
            "_parse_args",
            return_value=args,
        ), patch.object(
            check_deepsource_zero,
            "_wait_for_status_contexts",
            return_value=([{"context": "DeepSource: Python", "state": "success"}], []),
        ), patch.object(
            check_deepsource_zero,
            "_evaluate_visible_issues",
            return_value=(0, []),
        ), patch.object(check_deepsource_zero, "write_report", return_value=0) as write_report_mock:
            self.assertEqual(check_deepsource_zero.main(), 0)
        self.assertEqual(write_report_mock.call_args.args[0]["status"], "pass")

        with patch.dict("os.environ", {"GITHUB_TOKEN": "token"}, clear=False), patch.object(
            check_deepsource_zero,
            "_parse_args",
            return_value=args,
        ), patch.object(
            check_deepsource_zero,
            "_wait_for_status_contexts",
            side_effect=RuntimeError("provider timeout"),
        ), patch.object(check_deepsource_zero, "write_report", return_value=0) as write_report_mock:
            self.assertEqual(check_deepsource_zero.main(), 1)
        self.assertEqual(
            write_report_mock.call_args.args[0]["findings"],
            ["DeepSource request failed: provider timeout"],
        )

    def test_parse_args_render_markdown_and_script_entrypoint(self) -> None:
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

        script_path = Path("scripts/quality/check_deepsource_zero.py").resolve()
        root_text = str(Path.cwd().resolve())
        trimmed_sys_path = [item for item in sys.path if item != root_text]
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True), patch.object(
            sys,
            "argv",
            [str(script_path)],
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
