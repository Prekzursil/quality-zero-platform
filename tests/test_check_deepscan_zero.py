from __future__ import absolute_import

import os
import runpy
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from scripts.quality import check_deepscan_zero


def _placeholder_token(label: str) -> str:
    return f"{label}-placeholder"


class DeepScanZeroTests(unittest.TestCase):
    def test_github_check_context_mode_passes_when_deepscan_status_is_green(self) -> None:
        args = Namespace(repo="Prekzursil/quality-zero-platform", sha="abc123")
        api_token = _placeholder_token("api")

        with patch.object(
            check_deepscan_zero,
            "_github_status_payload",
            return_value={"statuses": [{"context": "DeepScan", "state": "success", "target_url": "https://deepscan.io"}]},
        ):
            open_issues, source_url, findings = check_deepscan_zero._evaluate_github_check_context(args, token=api_token)

        self.assertEqual(open_issues, 0)
        self.assertEqual(source_url, "https://deepscan.io")
        self.assertEqual(findings, [])

    def test_github_check_context_mode_fails_when_deepscan_status_is_missing(self) -> None:
        args = Namespace(repo="Prekzursil/quality-zero-platform", sha="abc123")
        api_token = _placeholder_token("api")

        with patch.dict("os.environ", {}, clear=True), patch.object(
            check_deepscan_zero,
            "_github_status_payload",
            return_value={"statuses": []},
        ):
            open_issues, source_url, findings = check_deepscan_zero._evaluate_github_check_context(args, token=api_token)

        self.assertIsNone(open_issues)
        self.assertEqual(source_url, "")
        self.assertIn("DeepScan GitHub status context is missing.", findings)

    def test_github_check_context_mode_allows_missing_status_on_push_main(self) -> None:
        args = Namespace(repo="Prekzursil/quality-zero-platform", sha="abc123")
        api_token = _placeholder_token("api")

        with patch.dict("os.environ", {"EVENT_NAME": "push"}, clear=False), patch.object(
            check_deepscan_zero,
            "_github_status_payload",
            return_value={"statuses": []},
        ):
            open_issues, source_url, findings = check_deepscan_zero._evaluate_github_check_context(args, token=api_token)

        self.assertEqual(open_issues, 0)
        self.assertEqual(source_url, "")
        self.assertEqual(findings, [])

    def test_validate_deepscan_inputs_accepts_github_check_context_mode(self) -> None:
        api_token = _placeholder_token("api")
        findings = check_deepscan_zero._validate_deepscan_inputs(
            token=api_token,
            policy_mode="github_check_context",
            open_issues_url="",
            github_token=_placeholder_token("github"),
            repo="Prekzursil/quality-zero-platform",
            sha="abc123",
        )

        self.assertEqual(findings, [])

    def test_extract_total_open_request_json_and_repo_sha_helpers_cover_edge_cases(self) -> None:
        api_token = _placeholder_token("api")
        self.assertEqual(check_deepscan_zero.extract_total_open({"nested": {"total": 2}}), 2)
        self.assertEqual(check_deepscan_zero.extract_total_open([{"open_issues": 1}]), 1)
        self.assertIsNone(check_deepscan_zero.extract_total_open({"nested": [{"ignored": True}]}))

        with patch("scripts.quality.check_deepscan_zero.load_json_https", return_value=(["invalid"], {})):
            with self.assertRaisesRegex(RuntimeError, "Unexpected DeepScan API response payload"):
                check_deepscan_zero._request_json("https://deepscan.io/test", api_token)
        with patch("scripts.quality.check_deepscan_zero.load_json_https", return_value=({"total": 0}, {})):
            self.assertEqual(check_deepscan_zero._request_json("https://deepscan.io/test", api_token), {"total": 0})
        with patch("scripts.quality.check_deepscan_zero.load_json_https", return_value=(["invalid"], {})):
            with self.assertRaisesRegex(RuntimeError, "Unexpected GitHub status response payload"):
                check_deepscan_zero._github_status_payload("Prekzursil/quality-zero-platform", "abc123", api_token)
        with patch("scripts.quality.check_deepscan_zero.load_json_https", return_value=({"statuses": []}, {})):
            self.assertEqual(
                check_deepscan_zero._github_status_payload("Prekzursil/quality-zero-platform", "abc123", api_token),
                {"statuses": []},
            )

        args = Namespace(repo="", sha="")
        with patch.dict(
            "os.environ",
            {"REPO_SLUG": "Prekzursil/quality-zero-platform", "TARGET_SHA": "abc123"},
            clear=False,
        ):
            self.assertEqual(check_deepscan_zero._github_repo(args), "Prekzursil/quality-zero-platform")
            self.assertEqual(check_deepscan_zero._github_sha(args), "abc123")

    def test_validate_helpers_and_open_issue_mode_cover_missing_input_paths(self) -> None:
        self.assertEqual(
            check_deepscan_zero._validate_github_check_context_inputs("", "", ""),
            [
                "GITHUB_TOKEN is missing for github_check_context mode.",
                "REPO_SLUG or GITHUB_REPOSITORY is missing for github_check_context mode.",
                "TARGET_SHA or GITHUB_SHA is missing for github_check_context mode.",
            ],
        )
        self.assertEqual(
            check_deepscan_zero._validate_open_issues_mode_inputs("", ""),
            [
                "DEEPSCAN_API_TOKEN is missing.",
                "DEEPSCAN_OPEN_ISSUES_URL is missing.",
            ],
        )

        with patch.object(check_deepscan_zero, "_request_json", return_value={"total": 4}):
            open_issues, source_url, findings = check_deepscan_zero._evaluate_open_issues_mode(
                "https://deepscan.io/project/issues",
                _placeholder_token("api"),
            )
        self.assertEqual(open_issues, 4)
        self.assertEqual(source_url, "https://deepscan.io/project/issues")
        self.assertEqual(findings, ["DeepScan reports 4 open issues (expected 0)."])

        with patch.object(check_deepscan_zero, "_request_json", return_value={"items": []}):
            open_issues, _, findings = check_deepscan_zero._evaluate_open_issues_mode(
                "https://deepscan.io/project/issues",
                _placeholder_token("api"),
            )
        self.assertIsNone(open_issues)
        self.assertEqual(findings, ["DeepScan response did not include a parseable total issue count."])

    def test_keyword_only_guards_reject_positional_and_unexpected_arguments(self) -> None:
        with self.assertRaisesRegex(TypeError, "expects keyword arguments only"):
            check_deepscan_zero._validate_deepscan_inputs("unexpected")

        with self.assertRaisesRegex(TypeError, "Unexpected _validate_deepscan_inputs parameters: extra"):
            check_deepscan_zero._validate_deepscan_inputs(
                token="token",
                policy_mode="open_issues_url",
                open_issues_url="https://deepscan.io/project/issues",
                github_token="github-token",
                repo="Prekzursil/quality-zero-platform",
                sha="abc123",
                extra=True,
            )

        args = Namespace(policy_mode="", repo="Prekzursil/quality-zero-platform", sha="abc123", github_context="DeepScan")
        with self.assertRaisesRegex(TypeError, "expects keyword arguments only"):
            check_deepscan_zero._evaluate_deepscan_policy(args, "unexpected")

        with self.assertRaisesRegex(TypeError, "Unexpected _evaluate_deepscan_policy parameters: extra"):
            check_deepscan_zero._evaluate_deepscan_policy(
                args,
                policy_mode="open_issues_url",
                token="token",
                github_token="github-token",
                open_issues_url="https://deepscan.io/project/issues",
                extra=True,
            )

    def test_status_helpers_and_policy_dispatch_cover_failure_branches(self) -> None:
        payload = {"statuses": [{"context": "DeepScan", "state": "failure", "target_url": "https://deepscan.io"}]}
        status = check_deepscan_zero._find_github_status(payload, "DeepScan")
        self.assertEqual(check_deepscan_zero._status_target_url(status), "https://deepscan.io")
        self.assertEqual(
            check_deepscan_zero._status_findings(status, "DeepScan"),
            ["DeepScan GitHub status is failure (expected success)."],
        )

        args = Namespace(policy_mode="", repo="Prekzursil/quality-zero-platform", sha="abc123", github_context="DeepScan")
        service_credential = "-".join(["service", "credential"])
        github_token = _placeholder_token("github")
        with patch.object(check_deepscan_zero, "_evaluate_github_check_context", return_value=(0, "https://deepscan.io", [])):
            result = check_deepscan_zero._evaluate_deepscan_policy(
                args,
                policy_mode="github_check_context",
                token=service_credential,
                github_token=github_token,
                open_issues_url="https://deepscan.io/project/issues",
            )
        self.assertEqual(result, (0, "https://deepscan.io", []))

        with patch.object(check_deepscan_zero, "_evaluate_open_issues_mode", return_value=(0, "https://deepscan.io/project/issues", [])):
            result = check_deepscan_zero._evaluate_deepscan_policy(
                args,
                policy_mode="open_issues_url",
                token=_placeholder_token("api"),
                github_token=github_token,
                open_issues_url="https://deepscan.io/project/issues",
            )
        self.assertEqual(result, (0, "https://deepscan.io/project/issues", []))

    def test_main_handles_missing_inputs_success_and_runtime_exceptions(self) -> None:
        args = Namespace(
            policy_mode="open_issues_url",
            repo="",
            sha="",
            github_context="DeepScan",
            token=str(),
            out_json="deepscan-zero/deepscan.json",
            out_md="deepscan-zero/deepscan.md",
        )
        with patch.dict("os.environ", {}, clear=True), patch.object(check_deepscan_zero, "_parse_args", return_value=args), patch.object(
            check_deepscan_zero, "write_report", return_value=0
        ) as write_report_mock:
            self.assertEqual(check_deepscan_zero.main(), 1)
        payload = write_report_mock.call_args.args[0]
        self.assertEqual(payload["status"], "fail")
        self.assertIn("DEEPSCAN_API_TOKEN is missing.", payload["findings"])

        success_args = Namespace(**{**args.__dict__, "token": _placeholder_token("api")})
        with patch.dict(
            "os.environ",
            {"DEEPSCAN_OPEN_ISSUES_URL": "https://deepscan.io/project/issues"},
            clear=False,
        ), patch.object(check_deepscan_zero, "_parse_args", return_value=success_args), patch.object(
            check_deepscan_zero, "_evaluate_deepscan_policy", return_value=(0, "https://deepscan.io/project/issues", [])
        ), patch.object(check_deepscan_zero, "write_report", return_value=0) as write_report_mock:
            self.assertEqual(check_deepscan_zero.main(), 0)
        self.assertEqual(write_report_mock.call_args.args[0]["status"], "pass")

        with patch.dict(
            "os.environ",
            {"DEEPSCAN_OPEN_ISSUES_URL": "https://deepscan.io/project/issues"},
            clear=False,
        ), patch.object(check_deepscan_zero, "_parse_args", return_value=success_args), patch.object(
            check_deepscan_zero,
            "_evaluate_deepscan_policy",
            side_effect=RuntimeError("provider timeout"),
        ), patch.object(check_deepscan_zero, "write_report", return_value=0) as write_report_mock:
            self.assertEqual(check_deepscan_zero.main(), 1)
        self.assertEqual(
            write_report_mock.call_args.args[0]["findings"],
            ["DeepScan API request failed: provider timeout"],
        )

        with patch.dict(
            "os.environ",
            {"DEEPSCAN_OPEN_ISSUES_URL": "https://deepscan.io/project/issues"},
            clear=False,
        ), patch.object(check_deepscan_zero, "_parse_args", return_value=success_args), patch.object(
            check_deepscan_zero, "_evaluate_deepscan_policy", return_value=(0, "https://deepscan.io/project/issues", [])
        ), patch.object(check_deepscan_zero, "write_report", return_value=9):
            self.assertEqual(check_deepscan_zero.main(), 9)

    def test_parse_args_render_markdown_and_script_entrypoint(self) -> None:
        with patch.object(sys, "argv", ["check_deepscan_zero.py"]):
            args = check_deepscan_zero._parse_args()
        self.assertEqual(args.github_context, "DeepScan")
        markdown = check_deepscan_zero._render_md(
            {
                "status": "pass",
                "open_issues": 0,
                "open_issues_url": "",
                "timestamp_utc": "2026-03-15T00:00:00+00:00",
                "findings": [],
            }
        )
        self.assertIn("`n/a`", markdown)
        self.assertIn("- None", markdown)

        script_path = Path("scripts/quality/check_deepscan_zero.py").resolve()
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

