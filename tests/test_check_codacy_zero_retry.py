"""Focused Codacy retry and fallback tests."""

from __future__ import absolute_import

import os
import runpy
import sys
import tempfile
import unittest
from argparse import Namespace
from email.message import Message
from pathlib import Path
from typing import List, Tuple
from urllib.error import HTTPError
from unittest.mock import patch

import scripts.quality.check_codacy_zero as check_codacy_zero
from scripts.quality import codacy_zero_support
from scripts.quality.check_codacy_zero import (
    CodacyQuery,
    CodacyRetryConfig,
    CodacyStatusResult,
    _build_payload,
    _write_codacy_report,
)


ANALYSIS_TOKEN = "analysis-token"


def _raise_runtime_error(message: str) -> None:
    """Raise one runtime error for callback tests."""
    raise RuntimeError(message)


class CodacyZeroRetryTests(unittest.TestCase):
    """Codacy retry and fallback tests."""

    @staticmethod
    def _base_query(
        *,
        provider: str = "gh",
        pull_request: str = "",
        sha: str = "",
    ) -> CodacyQuery:
        """Build one base query for retry-path tests."""
        return CodacyQuery(
            provider,
            "Prekzursil",
            "quality-zero-platform",
            pull_request=pull_request,
            sha=sha,
        )

    @staticmethod
    def _retry_config(
        provider_candidates: Tuple[str, ...],
        *,
        attempts: int = 1,
        pending_fn=check_codacy_zero._analysis_pending_message,
        sleep_seconds: float = 0.0,
    ) -> CodacyRetryConfig:
        """Build one retry config for retry-path tests."""
        return CodacyRetryConfig(
            provider_candidates=provider_candidates,
            attempts=attempts,
            pending_fn=pending_fn,
            sleep_seconds=sleep_seconds,
        )

    def test_load_codacy_findings_with_retry_retries_pull_request_404s(self) -> None:
        """Cover retrying pull-request 404s."""
        calls: List[int] = []

        def fake_query(*_args, **_kwargs):
            """Return a retryable 404 once, then a clean zero-issue result."""
            calls.append(len(calls))
            if len(calls) == 1:
                return (
                    None,
                    [],
                    HTTPError(
                        "https://api.codacy.com",
                        404,
                        "Not Found",
                        hdrs=Message(),
                        fp=None,
                    ),
                )
            return 0, [], None

        with (
            patch.object(check_codacy_zero, "SCOPED_ANALYSIS_RETRY_ATTEMPTS", 2),
            patch.object(check_codacy_zero.time, "sleep", return_value=None),
            patch.object(
                check_codacy_zero, "_query_codacy_open_issues", side_effect=fake_query
            ),
        ):
            open_issues, findings = check_codacy_zero.load_codacy_findings_with_retry(
                self._base_query(pull_request="49"),
                "token",
                self._retry_config(("gh",), attempts=2),
            )

        self.assertEqual((open_issues, findings), (0, []))
        self.assertEqual(len(calls), 2)

    def test_load_codacy_findings_with_retry_builds_default_config(self) -> None:
        """Cover retry loading with the default config."""
        with patch.object(
            check_codacy_zero,
            "_query_codacy_open_issues",
            return_value=(0, [], None),
        ):
            open_issues, findings = check_codacy_zero.load_codacy_findings_with_retry(
                self._base_query(),
                "token",
            )

        self.assertEqual((open_issues, findings), (0, []))

    def test_load_codacy_findings_with_retry_returns_last_findings_after_retry_budget(
        self,
    ) -> None:
        """Cover retry exhaustion with the last findings retained."""
        not_found = HTTPError(
            "https://api.codacy.com", 404, "Not Found", hdrs=Message(), fp=None
        )
        with (
            patch.object(check_codacy_zero, "SCOPED_ANALYSIS_RETRY_ATTEMPTS", 2),
            patch.object(check_codacy_zero.time, "sleep", return_value=None),
            patch.object(
                check_codacy_zero,
                "_query_codacy_open_issues",
                return_value=(
                    None,
                    ["Codacy API endpoint was not found for providers: gh, github."],
                    not_found,
                ),
            ) as query_mock,
        ):
            open_issues, findings = check_codacy_zero.load_codacy_findings_with_retry(
                self._base_query(pull_request="49"),
                "token",
                self._retry_config(("gh", "github"), attempts=2),
            )

        self.assertIsNone(open_issues)
        self.assertEqual(
            findings, ["Codacy API endpoint was not found for providers: gh, github."]
        )
        self.assertEqual(query_mock.call_count, 2)

    def test_load_codacy_findings_with_retry_waits_for_target_sha(self) -> None:
        """Cover waiting for the target SHA to arrive."""
        attempts: List[int] = []
        pending_responses = [
            "Codacy repository analysis is still on oldsha (waiting for targetsha).",
            None,
        ]

        def fake_query(*_args, **_kwargs):
            """Return one successful issue query while the status poll is pending."""
            attempts.append(len(attempts) + 1)
            return 0, [], None

        with patch.object(
            check_codacy_zero, "_query_codacy_open_issues", side_effect=fake_query
        ):
            open_issues, findings = check_codacy_zero.load_codacy_findings_with_retry(
                self._base_query(sha="targetsha"),
                "token",
                self._retry_config(
                    ("gh",),
                    attempts=2,
                    pending_fn=lambda _query, _token: pending_responses.pop(0),
                ),
            )

        self.assertEqual((open_issues, findings), (0, []))
        self.assertEqual(attempts, [1, 2])

    def test_load_codacy_findings_with_retry_reports_pending_analysis_after_budget(
        self,
    ) -> None:
        """Cover pending-analysis reporting after the retry budget is exhausted."""
        with patch.object(
            check_codacy_zero,
            "_query_codacy_open_issues",
            return_value=(0, [], None),
        ):
            open_issues, findings = check_codacy_zero.load_codacy_findings_with_retry(
                self._base_query(sha="targetsha"),
                "token",
                self._retry_config(
                    ("gh",),
                    pending_fn=lambda _query, _token: (
                        "Codacy repository analysis is not available yet."
                    ),
                ),
            )

        self.assertEqual(open_issues, 0)
        self.assertEqual(findings, ["Codacy repository analysis is not available yet."])

    def test_load_codacy_findings_with_retry_does_not_retry_without_pull_request(
        self,
    ) -> None:
        """Cover the unscoped one-shot retry path."""
        with patch.object(
            check_codacy_zero,
            "_query_codacy_open_issues",
            return_value=(0, [], None),
        ) as query_mock:
            open_issues, findings = check_codacy_zero.load_codacy_findings_with_retry(
                self._base_query(),
                "token",
                self._retry_config(("gh",)),
            )

        self.assertEqual((open_issues, findings), (0, []))
        query_mock.assert_called_once()

    def test_load_codacy_findings_with_retry_returns_pr_success(self) -> None:
        """Cover immediate pull-request success."""
        with (
            patch.object(
                check_codacy_zero,
                "_query_codacy_open_issues",
                return_value=(0, [], None),
            ) as query_mock,
            patch.object(check_codacy_zero.time, "sleep", return_value=None) as sleep_mock,
        ):
            open_issues, findings = check_codacy_zero.load_codacy_findings_with_retry(
                self._base_query(pull_request="49"),
                "token",
                self._retry_config(("gh",), attempts=2),
            )

        self.assertEqual((open_issues, findings), (0, []))
        query_mock.assert_called_once()
        sleep_mock.assert_not_called()

    def test_load_codacy_findings_with_retry_reports_pending_status_failures(
        self,
    ) -> None:
        """Cover pending-status failures."""
        with patch.object(
            check_codacy_zero,
            "_query_codacy_open_issues",
            return_value=(0, [], None),
        ):
            open_issues, findings = check_codacy_zero.load_codacy_findings_with_retry(
                self._base_query(sha="targetsha"),
                "token",
                self._retry_config(
                    ("gh",),
                    pending_fn=lambda _query, _token: _raise_runtime_error("status broke"),
                ),
            )

        self.assertEqual(open_issues, 0)
        self.assertEqual(
            findings, ["Codacy analysis status request failed: status broke"]
        )

    def test_commit_scope_fallback_uses_commit_query_when_pr_payload_is_stale(
        self,
    ) -> None:
        """Cover commit-scope fallback success when PR payload is stale."""
        self.assertIsNone(
            check_codacy_zero._commit_scope_fallback(
                self._base_query(pull_request="68", sha="targetsha"),
                "token",
                ("gh",),
                ["Codacy reports 2 open issues (expected 0)."],
            )
        )
        with patch.object(
            check_codacy_zero,
            "_query_codacy_open_issues",
            return_value=(0, [], None),
        ) as query_mock:
            self.assertEqual(
                check_codacy_zero._commit_scope_fallback(
                    self._base_query(pull_request="68", sha="targetsha"),
                    "token",
                    ("gh", "github"),
                    [
                        "Codacy reports 18 open issues (expected 0).",
                        (
                            "Codacy analysis for pull request 68 issues is still on "
                            "oldsha (waiting for targetsha)."
                        ),
                    ],
                ),
                (0, []),
            )
        commit_query = query_mock.call_args.args[0]
        self.assertEqual(commit_query.pull_request, "")
        self.assertEqual(commit_query.sha, "targetsha")
        self.assertEqual(query_mock.call_args.args[2], ("gh", "github"))

    def test_commit_scope_fallback_skips_when_target_sha_is_missing(self) -> None:
        """Cover the commit-scope guard when the target sha is missing."""
        self.assertIsNone(
            check_codacy_zero._commit_scope_fallback(
                self._base_query(pull_request="68", sha=""),
                "token",
                ("gh",),
                [
                    (
                        "Codacy analysis for pull request 68 issues is still on "
                        "oldsha (waiting for targetsha)."
                    )
                ],
            )
        )

    def test_commit_scope_fallback_skips_when_commit_scope_is_still_pending(
        self,
    ) -> None:
        """Cover the commit-scope guard while commit-scoped data is pending."""
        with patch.object(
            check_codacy_zero,
            "_query_codacy_open_issues",
            return_value=(None, [], RuntimeError("still pending")),
        ):
            self.assertIsNone(
                check_codacy_zero._commit_scope_fallback(
                    self._base_query(pull_request="68", sha="targetsha"),
                    "token",
                    ("gh",),
                    [
                        (
                            "Codacy analysis for pull request 68 issues is still on "
                            "oldsha (waiting for targetsha)."
                        )
                    ],
                )
            )

    def test_resolve_codacy_status_prefers_commit_scope_when_pr_payload_is_stale(
        self,
    ) -> None:
        """Resolve PR status from commit-scoped Codacy data when PR data is stale."""
        args = Namespace(
            provider="gh",
            owner="Prekzursil",
            repo="quality-zero-platform",
            pull_request="68",
            sha="targetsha",
            token=ANALYSIS_TOKEN,
            policy_mode="ratchet",
            out_json="codacy-zero/codacy.json",
            out_md="codacy-zero/codacy.md",
        )
        with (
            patch.object(
                check_codacy_zero,
                "load_codacy_findings_with_retry",
                return_value=(
                    18,
                    [
                        "Codacy reports 18 open issues (expected 0).",
                        (
                            "Codacy analysis for pull request 68 issues is still on "
                            "oldsha (waiting for targetsha)."
                        ),
                    ],
                ),
            ),
            patch.object(
                check_codacy_zero,
                "_query_codacy_open_issues",
                return_value=(0, [], None),
            ),
        ):
            result = check_codacy_zero._resolve_codacy_status(args)

        self.assertEqual(result.status, "pass")
        self.assertEqual(result.open_issues, 0)
        self.assertEqual(result.findings, [])

    def test_support_detects_stale_pull_request_findings(self) -> None:
        """Cover the shared stale PR finding matcher."""
        self.assertTrue(
            codacy_zero_support.stale_pull_request_findings(
                "68",
                [
                    (
                        "Codacy analysis for pull request 68 issues is still on "
                        "oldsha (waiting for targetsha)."
                    )
                ],
            )
        )
        self.assertFalse(
            codacy_zero_support.stale_pull_request_findings(
                "68",
                ["Codacy reports 1 open issue (expected 0)."],
            )
        )
        self.assertFalse(
            codacy_zero_support.stale_pull_request_findings(
                "",
                [
                    (
                        "Codacy analysis for pull request 68 issues is still on "
                        "oldsha (waiting for targetsha)."
                    )
                ],
            )
        )

    def test_payload_and_report_helpers(self) -> None:
        """Cover payload and report helpers."""
        payload = _build_payload(
            Namespace(provider="gh", owner="Prekzursil", repo="quality-zero-platform"),
            CodacyStatusResult(
                status="pass", findings=["done"], open_issues=0, pull_request=""
            ),
        )
        self.assertIn("- done", check_codacy_zero._render_md(payload))
        with patch.object(
            check_codacy_zero, "write_report", return_value=0
        ) as write_report_mock:
            self.assertEqual(
                _write_codacy_report(
                    Namespace(
                        out_json="codacy-zero/codacy.json",
                        out_md="codacy-zero/codacy.md",
                    ),
                    payload,
                ),
                0,
            )
        self.assertEqual(
            write_report_mock.call_args.kwargs["render_md"],
            check_codacy_zero._render_md,
        )

    def test_parse_args_and_script_entrypoint(self) -> None:
        """Cover parse args and the direct script entrypoint."""
        with patch.object(
            sys,
            "argv",
            [
                "check_codacy_zero.py",
                "--owner",
                "Prekzursil",
                "--repo",
                "quality-zero-platform",
            ],
        ):
            args = check_codacy_zero._parse_args()
        self.assertEqual(args.provider, "gh")
        self.assertEqual(args.out_json, "codacy-zero/codacy.json")

        script_path = Path("scripts/quality/check_codacy_zero.py").resolve()
        root_text = str(Path.cwd().resolve())
        trimmed_sys_path = [item for item in sys.path if item != root_text]
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict("os.environ", {}, clear=True),
            patch.object(
                sys,
                "argv",
                [
                    str(script_path),
                    "--owner",
                    "Prekzursil",
                    "--repo",
                    "quality-zero-platform",
                ],
            ),
            patch.object(sys, "path", trimmed_sys_path[:]),
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
