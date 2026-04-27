"""Test check deepscan zero."""

from __future__ import absolute_import

import sys
import unittest
from argparse import Namespace
from unittest.mock import patch

from tests.script_entrypoint_support import (
    assert_in_process_entrypoint_failure,
    assert_main_reports_provider_failure,
    run_script_entrypoint_failure,
)

from scripts.quality import check_deepscan_zero


def _placeholder_token(label: str) -> str:
    """Handle placeholder token."""
    return f"{label}-placeholder"


class DeepScanZeroTests(unittest.TestCase):
    """Deep Scan Zero Tests."""

    def _assert_request_payload_guards(self, api_token: str) -> None:
        """Exercise request-json and status-payload guards."""
        with (
            patch(
                "scripts.quality.check_deepscan_zero.load_json_https",
                return_value=(["invalid"], {}),
            ),
            self.assertRaisesRegex(
                RuntimeError, "Unexpected DeepScan API response payload"
            ),
        ):
            check_deepscan_zero._request_json("https://deepscan.io/test", api_token)
        with patch(
            "scripts.quality.check_deepscan_zero.load_json_https",
            return_value=({"total": 0}, {}),
        ):
            self.assertEqual(
                check_deepscan_zero._request_json(
                    "https://deepscan.io/test", api_token
                ),
                {"total": 0},
            )
        with (
            patch(
                "scripts.quality.common.load_json_https",
                return_value=(["invalid"], {}),
            ),
            self.assertRaisesRegex(
                RuntimeError, "Unexpected GitHub status response payload"
            ),
        ):
            check_deepscan_zero._github_status_payload(
                "Prekzursil/quality-zero-platform", "abc123", api_token
            )
        with patch(
            "scripts.quality.common.load_json_https",
            return_value=({"statuses": []}, {}),
        ):
            self.assertEqual(
                check_deepscan_zero._github_status_payload(
                    "Prekzursil/quality-zero-platform", "abc123", api_token
                ),
                {"statuses": []},
            )

    def _assert_policy_dispatch(self, args: Namespace, github_token: str) -> None:
        """Assert both DeepScan policy-dispatch branches."""
        with patch.object(
            check_deepscan_zero,
            "_evaluate_github_check_context",
            return_value=(0, "https://deepscan.io", []),
        ):
            self.assertEqual(
                check_deepscan_zero._evaluate_deepscan_policy(
                    args,
                    policy_mode="github_check_context",
                    token="-".join(["service", "credential"]),
                    github_token=github_token,
                    open_issues_url="https://deepscan.io/project/issues",
                ),
                (0, "https://deepscan.io", []),
            )
        with patch.object(
            check_deepscan_zero,
            "_evaluate_open_issues_mode",
            return_value=(0, "https://deepscan.io/project/issues", []),
        ):
            self.assertEqual(
                check_deepscan_zero._evaluate_deepscan_policy(
                    args,
                    policy_mode="open_issues_url",
                    token=_placeholder_token("api"),
                    github_token=github_token,
                    open_issues_url="https://deepscan.io/project/issues",
                ),
                (0, "https://deepscan.io/project/issues", []),
            )

    def _assert_main_result(self, scenario: dict) -> None:
        """Exercise one DeepScan main-path scenario."""
        args = scenario["args"]
        env = scenario.get("env", {})
        with patch.dict("os.environ", env, clear=not env), patch.object(
            check_deepscan_zero, "_parse_args", return_value=args
        ), patch.object(
            check_deepscan_zero,
            "write_report",
            return_value=scenario.get("write_report_result", 0),
        ) as write_report_mock:
            policy_result = scenario.get("policy_result")
            if policy_result is None:
                self.assertEqual(check_deepscan_zero.main(), scenario["expected_code"])
            else:
                with patch.object(
                    check_deepscan_zero,
                    "_evaluate_deepscan_policy",
                    return_value=policy_result,
                ):
                    self.assertEqual(
                        check_deepscan_zero.main(), scenario["expected_code"]
                    )
        payload = write_report_mock.call_args.args[0]
        self.assertEqual(payload["status"], scenario["expected_status"])
        expected_finding = scenario.get("expected_finding")
        if expected_finding is not None:
            self.assertIn(expected_finding, payload["findings"])

    def test_policy_mode_defaults_to_github_check_context(self) -> None:
        """Cover policy mode defaults to github check context."""
        args = Namespace(policy_mode="", repo="", sha="", github_context="DeepScan")

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                check_deepscan_zero._policy_mode(args), "github_check_context"
            )

    def test_github_check_context_mode_passes_when_deepscan_status_is_green(
        self,
    ) -> None:
        """Cover github check context mode passes when deepscan status is green."""
        args = Namespace(repo="Prekzursil/quality-zero-platform", sha="abc123")
        api_token = _placeholder_token("api")

        with patch.object(
            check_deepscan_zero,
            "_github_status_payload",
            return_value={
                "statuses": [
                    {
                        "context": "DeepScan",
                        "state": "success",
                        "target_url": "https://deepscan.io",
                    }
                ]
            },
        ):
            open_issues, source_url, findings = (
                check_deepscan_zero._evaluate_github_check_context(
                    args, token=api_token
                )
            )

        self.assertEqual(open_issues, 0)
        self.assertEqual(source_url, "https://deepscan.io")
        self.assertEqual(findings, [])

    def test_github_check_context_mode_fails_when_deepscan_status_is_missing(
        self,
    ) -> None:
        """Cover github check context mode fails when deepscan status is missing."""
        args = Namespace(repo="Prekzursil/quality-zero-platform", sha="abc123")
        api_token = _placeholder_token("api")

        with patch.dict("os.environ", {}, clear=True), patch.object(
            check_deepscan_zero,
            "_github_status_payload",
            return_value={"statuses": []},
        ):
            open_issues, source_url, findings = (
                check_deepscan_zero._evaluate_github_check_context(
                    args, token=api_token
                )
            )

        self.assertIsNone(open_issues)
        self.assertEqual(source_url, "")
        self.assertIn("DeepScan GitHub status context is missing.", findings)

    def test_github_check_context_mode_allows_missing_status_on_push_main(self) -> None:
        """Cover github check context mode allows missing status on push main."""
        args = Namespace(repo="Prekzursil/quality-zero-platform", sha="abc123")
        api_token = _placeholder_token("api")

        with patch.dict(
            "os.environ", {"EVENT_NAME": "push"}, clear=False
        ), patch.object(
            check_deepscan_zero,
            "_github_status_payload",
            return_value={"statuses": []},
        ):
            open_issues, source_url, findings = (
                check_deepscan_zero._evaluate_github_check_context(
                    args, token=api_token
                )
            )

        self.assertEqual(open_issues, 0)
        self.assertEqual(source_url, "")
        self.assertEqual(findings, [])

    def test_validate_deepscan_inputs_accepts_github_check_context_mode(self) -> None:
        """Cover validate deepscan inputs accepts github check context mode."""
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

    def test_extract_total_open_request_json_and_repo_sha_helpers_cover_edge_cases(
        self,
    ) -> None:
        """Cover extract total open request json and repo sha edge cases."""
        api_token = _placeholder_token("api")
        self.assertEqual(
            check_deepscan_zero.extract_total_open({"nested": {"total": 2}}), 2
        )
        self.assertEqual(
            check_deepscan_zero.extract_total_open([{"open_issues": 1}]), 1
        )
        self.assertIsNone(
            check_deepscan_zero.extract_total_open({"nested": [{"ignored": True}]})
        )

        self._assert_request_payload_guards(api_token)

        args = Namespace(repo="", sha="")
        with patch.dict(
            "os.environ",
            {"REPO_SLUG": "Prekzursil/quality-zero-platform", "TARGET_SHA": "abc123"},
            clear=False,
        ):
            self.assertEqual(
                check_deepscan_zero._github_repo(args),
                "Prekzursil/quality-zero-platform",
            )
            self.assertEqual(check_deepscan_zero._github_sha(args), "abc123")

    def test_validate_helpers_and_open_issue_mode_cover_missing_input_paths(
        self,
    ) -> None:
        """Cover validate helpers and open issue mode cover missing input paths."""
        self.assertEqual(
            check_deepscan_zero._validate_github_check_context_inputs("", "", ""),
            [
                "GITHUB_TOKEN is missing for github_check_context mode.",
                (
                    "REPO_SLUG or GITHUB_REPOSITORY is missing for "
                    "github_check_context mode."
                ),
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

        with patch.object(
            check_deepscan_zero, "_request_json", return_value={"total": 4}
        ):
            open_issues, source_url, findings = (
                check_deepscan_zero._evaluate_open_issues_mode(
                    "https://deepscan.io/project/issues",
                    _placeholder_token("api"),
                )
            )
        self.assertEqual(open_issues, 4)
        self.assertEqual(source_url, "https://deepscan.io/project/issues")
        self.assertEqual(findings, ["DeepScan reports 4 open issues (expected 0)."])

        with patch.object(
            check_deepscan_zero, "_request_json", return_value={"items": []}
        ):
            open_issues, _, findings = check_deepscan_zero._evaluate_open_issues_mode(
                "https://deepscan.io/project/issues",
                _placeholder_token("api"),
            )
        self.assertIsNone(open_issues)
        self.assertEqual(
            findings,
            ["DeepScan response did not include a parseable total issue count."],
        )

    def test_keyword_only_guards_reject_positional_and_unexpected_arguments(
        self,
    ) -> None:
        """Cover keyword only guards reject positional and unexpected arguments."""
        with self.assertRaisesRegex(TypeError, "expects keyword arguments only"):
            check_deepscan_zero._validate_deepscan_inputs("unexpected")

        with self.assertRaisesRegex(
            TypeError, "Unexpected _validate_deepscan_inputs parameters: extra"
        ):
            check_deepscan_zero._validate_deepscan_inputs(
                token=_placeholder_token("api"),
                policy_mode="open_issues_url",
                open_issues_url="https://deepscan.io/project/issues",
                github_token=_placeholder_token("github"),
                repo="Prekzursil/quality-zero-platform",
                sha="abc123",
                extra=True,
            )

        args = Namespace(
            policy_mode="",
            repo="Prekzursil/quality-zero-platform",
            sha="abc123",
            github_context="DeepScan",
        )
        with self.assertRaisesRegex(TypeError, "expects keyword arguments only"):
            check_deepscan_zero._evaluate_deepscan_policy(args, "unexpected")

        with self.assertRaisesRegex(
            TypeError, "Unexpected _evaluate_deepscan_policy parameters: extra"
        ):
            check_deepscan_zero._evaluate_deepscan_policy(
                args,
                policy_mode="open_issues_url",
                token=_placeholder_token("api"),
                github_token=_placeholder_token("github"),
                open_issues_url="https://deepscan.io/project/issues",
                extra=True,
            )

    def test_status_helpers_and_policy_dispatch_cover_failure_branches(self) -> None:
        """Cover status helpers and policy dispatch cover failure branches."""
        payload = {
            "statuses": [
                {
                    "context": "DeepScan",
                    "state": "failure",
                    "target_url": "https://deepscan.io",
                }
            ]
        }
        status = check_deepscan_zero._find_github_status(payload, "DeepScan")
        self.assertEqual(
            check_deepscan_zero._status_target_url(status), "https://deepscan.io"
        )
        self.assertEqual(
            check_deepscan_zero._status_findings(status, "DeepScan"),
            ["DeepScan GitHub status is failure (expected success)."],
        )

        args = Namespace(
            policy_mode="",
            repo="Prekzursil/quality-zero-platform",
            sha="abc123",
            github_context="DeepScan",
        )
        github_token = _placeholder_token("github")
        self._assert_policy_dispatch(args, github_token)

    def test_main_handles_missing_inputs_success_and_runtime_exceptions(self) -> None:
        """Cover main handles missing inputs success and runtime exceptions."""
        args = Namespace(
            policy_mode="open_issues_url",
            repo="",
            sha="",
            github_context="DeepScan",
            token=str(),
            out_json="deepscan-zero/deepscan.json",
            out_md="deepscan-zero/deepscan.md",
        )
        self._assert_main_result(
            {
                "args": args,
                "env": {},
                "expected_code": 1,
                "expected_status": "fail",
                "expected_finding": "DEEPSCAN_API_TOKEN is missing.",
            }
        )

        success_args = Namespace(
            **{**args.__dict__, "token": _placeholder_token("api")}
        )
        deepscan_env = {"DEEPSCAN_OPEN_ISSUES_URL": "https://deepscan.io/project/issues"}
        self._assert_main_result(
            {
                "args": success_args,
                "env": deepscan_env,
                "policy_result": (0, "https://deepscan.io/project/issues", []),
                "expected_code": 0,
                "expected_status": "pass",
            }
        )

        assert_main_reports_provider_failure(
            self,
            check_deepscan_zero,
            {
                "env": deepscan_env,
                "args": success_args,
                "operation_name": "_evaluate_deepscan_policy",
                "failure_message": "provider timeout",
                "expected_finding": "DeepScan API request failed: provider timeout",
            },
        )

        self._assert_main_result(
            {
                "args": success_args,
                "env": deepscan_env,
                "policy_result": (0, "https://deepscan.io/project/issues", []),
                "write_report_result": 9,
                "expected_code": 9,
                "expected_status": "pass",
            }
        )

    def test_parse_args_render_markdown_and_script_entrypoint(self) -> None:
        """Cover parse args render markdown and script entrypoint."""
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

        self.assertEqual(
            run_script_entrypoint_failure("scripts/quality/check_deepscan_zero.py"),
            1,
        )
        assert_in_process_entrypoint_failure(
            self, "scripts/quality/check_deepscan_zero.py"
        )
