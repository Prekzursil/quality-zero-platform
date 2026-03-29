"""Focused Codacy analysis and status-path tests."""

from __future__ import absolute_import

import unittest
from argparse import Namespace
from email.message import Message
from typing import List, Tuple
from urllib.error import HTTPError
from unittest.mock import Mock, patch

import scripts.quality.check_codacy_zero as check_codacy_zero
from scripts.quality import codacy_zero_support
from scripts.quality.check_codacy_zero import (
    CodacyQuery,
    _build_retry_config,
    _query_codacy_candidate,
    _query_codacy_open_issues,
)


class CodacyZeroAnalysisTests(unittest.TestCase):
    """Codacy analysis and status-path tests."""

    @staticmethod
    def _base_query(
        *,
        provider: str = "gh",
        pull_request: str = "",
        sha: str = "",
    ) -> CodacyQuery:
        """Build one base query for analysis-path tests."""
        return CodacyQuery(
            provider,
            "Prekzursil",
            "quality-zero-platform",
            pull_request=pull_request,
            sha=sha,
        )

    def test_build_retry_config_for_scoped_and_unscoped_queries(self) -> None:
        """Cover build retry config for scoped and unscoped queries."""
        unscoped = _build_retry_config(self._base_query(), ["gh"])
        self.assertEqual(unscoped.attempts, 1)
        self.assertEqual(unscoped.provider_candidates, ("gh",))
        scoped = _build_retry_config(
            self._base_query(pull_request="5", sha="targetsha"),
            ["gh", "github"],
            sleep_seconds=-1.0,
        )
        self.assertEqual(
            scoped.attempts, check_codacy_zero.SCOPED_ANALYSIS_RETRY_ATTEMPTS
        )
        self.assertEqual(scoped.sleep_seconds, 0.0)

    def test_request_analysis_status_validates_payload_shape(self) -> None:
        """Cover request analysis status validates payload shape."""
        with (
            patch(
                "scripts.quality.check_codacy_zero.load_json_https",
                return_value=("bad", {}),
            ),
            self.assertRaisesRegex(
                RuntimeError, "Unexpected Codacy analysis status payload"
            ),
        ):
            check_codacy_zero._request_analysis_status(
                "https://app.codacy.com/api/v3/test", "token"
            )
        with patch(
            "scripts.quality.check_codacy_zero.load_json_https",
            return_value=({"data": {}}, {}),
        ):
            self.assertEqual(
                check_codacy_zero._request_analysis_status(
                    "https://app.codacy.com/api/v3/test", "token"
                ),
                {"data": {}},
            )

    def test_analysis_pending_message_reports_pull_request_status_states(self) -> None:
        """Cover pull-request status-state pending messages."""
        pr_query = self._base_query(pull_request="5", sha="targetsha")
        scenarios = [
            (
                {"isAnalysing": True},
                "Codacy is still analysing pull request 5.",
            ),
            (
                {"pullRequest": {}},
                "Codacy analysis for pull request 5 is not available yet.",
            ),
            (
                {"pullRequest": {"headCommitSha": "oldsha"}},
                (
                    "Codacy analysis for pull request 5 is still on oldsha "
                    "(waiting for targetsha)."
                ),
            ),
        ]
        for payload, expected in scenarios:
            with patch.object(
                check_codacy_zero,
                "_request_analysis_status",
                return_value=payload,
            ):
                self.assertEqual(
                    check_codacy_zero._analysis_pending_message(pr_query, "token"),
                    expected,
                )

    def test_analysis_pending_message_reads_pull_request_issue_payload(self) -> None:
        """Cover issue-payload pending messages once PR status reaches target SHA."""
        pr_query = self._base_query(pull_request="5", sha="targetsha")
        with (
            patch.object(
                check_codacy_zero,
                "_request_analysis_status",
                return_value={"pullRequest": {"headCommitSha": "targetsha"}},
            ),
            patch.object(
                check_codacy_zero,
                "_request_json",
                return_value={"analyzed": False},
            ),
        ):
            self.assertEqual(
                check_codacy_zero._analysis_pending_message(pr_query, "token"),
                "Codacy issues for pull request 5 are not available yet.",
            )

        with (
            patch.object(
                check_codacy_zero,
                "_request_analysis_status",
                return_value={"pullRequest": {"headCommitSha": "targetsha"}},
            ),
            patch.object(
                check_codacy_zero,
                "_request_json",
                return_value={
                    "analyzed": True,
                    "data": [{"commitIssue": {"commitInfo": {"sha": "oldsha"}}}],
                },
            ),
        ):
            self.assertEqual(
                check_codacy_zero._analysis_pending_message(pr_query, "token"),
                (
                    "Codacy analysis for pull request 5 issues is still on oldsha "
                    "(waiting for targetsha)."
                ),
            )

        with (
            patch.object(
                check_codacy_zero,
                "_request_analysis_status",
                return_value={"pullRequest": {"headCommitSha": "targetsha"}},
            ),
            patch.object(
                check_codacy_zero,
                "_request_json",
                return_value={"analyzed": True, "data": []},
            ),
        ):
            self.assertIsNone(
                check_codacy_zero._analysis_pending_message(pr_query, "token")
            )

    def test_analysis_pending_message_tracks_repository_state(self) -> None:
        """Cover repository-analysis pending-state transitions."""
        repo_query = self._base_query(sha="targetsha")
        scenarios = [
            (
                {"data": {}},
                "Codacy analysis for repository is not available yet.",
            ),
            (
                {"data": {"lastAnalysedCommit": {"sha": "oldsha"}}},
                (
                    "Codacy analysis for repository is still on oldsha "
                    "(waiting for targetsha)."
                ),
            ),
            (
                {"data": {"lastAnalysedCommit": {"sha": "targetsha"}}},
                "Codacy repository analysis has not finished yet.",
            ),
        ]
        for payload, expected in scenarios:
            with patch.object(
                check_codacy_zero,
                "_request_analysis_status",
                return_value=payload,
            ):
                self.assertEqual(
                    check_codacy_zero._analysis_pending_message(repo_query, "token"),
                    expected,
                )
        with patch.object(
            check_codacy_zero,
            "_request_analysis_status",
            return_value={
                "data": {
                    "lastAnalysedCommit": {"sha": "targetsha", "endedAnalysis": "done"}
                }
            },
        ):
            self.assertIsNone(
                check_codacy_zero._analysis_pending_message(repo_query, "token")
            )

    def test_open_issue_query_paths(self) -> None:
        """Cover open issue query paths."""
        responses = [Exception("sentinel"), {"total": 0}]

        def fake_request(url: str, token: str, *, method: str = "GET", data=None):
            """Return one mocked request payload for the open-issue query path."""
            current = responses.pop(0)
            if isinstance(current, Exception):
                raise HTTPError(url, 404, "Not Found", hdrs=Message(), fp=None)
            return current

        with patch(
            "scripts.quality.check_codacy_zero._request_json",
            side_effect=fake_request,
        ):
            self.assertEqual(
                _query_codacy_open_issues(self._base_query(), "token", ["custom", "gh"]),
                (0, [], None),
            )

        captured: List[Tuple[str, str, object | None]] = []

        def capture_request(url: str, token: str, *, method: str = "GET", data=None):
            """Capture the request details for one open-issue query call."""
            captured.append((url, method, data))
            return {"total": 0}

        with patch(
            "scripts.quality.check_codacy_zero._request_json",
            side_effect=capture_request,
        ):
            self.assertEqual(
                _query_codacy_open_issues(
                    self._base_query(pull_request="5"), "token", ["gh"]
                ),
                (0, [], None),
            )
        self.assertEqual(
            captured,
            [
                (
                    "https://app.codacy.com/api/v3/analysis/organizations/gh/"
                    "Prekzursil/repositories/quality-zero-platform/"
                    "pull-requests/5/issues?status=new&limit=1",
                    "GET",
                    None,
                )
            ],
        )

    def test_open_issue_http_error_paths(self) -> None:
        """Cover authorized, unauthorized, and runtime error paths for issue queries."""
        error = HTTPError(
            "https://api.codacy.com", 401, "Unauthorized", hdrs=Message(), fp=None
        )
        with (
            patch(
                "scripts.quality.check_codacy_zero._query_codacy_provider",
                side_effect=error,
            ),
            patch(
                "scripts.quality.check_codacy_zero._query_codacy_public_repository_issues",
                return_value=(0, []),
            ) as fallback_mock,
        ):
            self.assertEqual(
                _query_codacy_open_issues(self._base_query(), "token", ["gh"]),
                (0, [], None),
            )
        fallback_mock.assert_called_once_with(
            "gh", "Prekzursil", "quality-zero-platform"
        )

        with patch(
            "scripts.quality.check_codacy_zero._query_codacy_provider",
            side_effect=HTTPError("u", 500, "Boom", hdrs=Message(), fp=None),
        ):
            open_issues, findings, exc = _query_codacy_open_issues(
                self._base_query(), "token", ["gh"]
            )
        self.assertIsNone(open_issues)
        self.assertEqual(findings, ["Codacy API request failed: HTTP 500"])
        self.assertIsNotNone(exc)

        with patch(
            "scripts.quality.check_codacy_zero._query_codacy_provider",
            side_effect=RuntimeError("network broke"),
        ):
            open_issues, findings, exc = _query_codacy_open_issues(
                self._base_query(), "token", ["gh"]
            )
        self.assertIsNone(open_issues)
        self.assertEqual(findings, ["Codacy API request failed: network broke"])
        self.assertIsInstance(exc, RuntimeError)

    def test_open_issue_not_found_paths(self) -> None:
        """Cover not-found provider alias handling for issue queries."""
        def fake_provider(*_args, **_kwargs):
            """Raise one provider 404 to cover not-found alias handling."""
            raise HTTPError(
                "https://api.codacy.com", 404, "Not Found", hdrs=Message(), fp=None
            )

        with patch(
            "scripts.quality.check_codacy_zero._query_codacy_provider",
            side_effect=fake_provider,
        ):
            open_issues, findings, exc = _query_codacy_open_issues(
                self._base_query(),
                "token",
                ["custom", "github"],
            )
        self.assertIsNone(open_issues)
        self.assertIn("Codacy API endpoint was not found", findings[0])
        self.assertIsNotNone(exc)

    def test_fallback_and_http_error_helpers_cover_remaining_branches(self) -> None:
        """Cover fallback and direct HTTP-error helper branches."""
        self.assertIsNone(
            check_codacy_zero._fallback_public_issues(
                self._base_query(pull_request="5")
            )
        )
        error = HTTPError(
            "https://api.codacy.com", 401, "Unauthorized", hdrs=Message(), fp=None
        )
        with patch(
            "scripts.quality.check_codacy_zero._fallback_public_issues",
            return_value=None,
        ):
            self.assertEqual(
                check_codacy_zero._handle_codacy_http_error(error, self._base_query()),
                (None, ["Codacy API request failed: HTTP 401"], error, True),
            )
        with patch(
            "scripts.quality.check_codacy_zero._fallback_public_issues",
            return_value=(None, [], RuntimeError("fallback broke")),
        ):
            open_issues, findings, exc, should_return = (
                check_codacy_zero._handle_codacy_http_error(
                    error,
                    self._base_query(),
                )
            )
        self.assertIsNone(open_issues)
        self.assertEqual(findings, [])
        self.assertIsInstance(exc, RuntimeError)
        self.assertFalse(should_return)

    def test_direct_wrapper_helpers_delegate_to_support_functions(self) -> None:
        """Cover direct wrapper helper delegation."""
        error = HTTPError(
            "https://api.codacy.com", 401, "Unauthorized", hdrs=Message(), fp=None
        )
        with patch(
            "scripts.quality.check_codacy_zero._fallback_public_issues",
            return_value=(0, [], None),
        ):
            self.assertEqual(
                check_codacy_zero._unauthorized_http_result(error, self._base_query()),
                (0, [], None, True),
            )
        self.assertEqual(
            check_codacy_zero._sha_wait_message("repository", "oldsha", "targetsha"),
            (
                "Codacy analysis for repository is still on oldsha "
                "(waiting for targetsha)."
            ),
        )
        self.assertIsNone(
            check_codacy_zero._pull_request_pending_message(
                {"pullRequest": {"headCommitSha": "targetsha"}},
                self._base_query(pull_request="5"),
                "targetsha",
            )
        )
        self.assertIsNone(
            check_codacy_zero._repository_pending_message(
                {
                    "data": {
                        "lastAnalysedCommit": {
                            "sha": "targetsha",
                            "endedAnalysis": "done",
                        }
                    }
                },
                "targetsha",
            )
        )

    def test_codacy_support_pending_message_handles_empty_sha_and_non_pr(self) -> None:
        """Cover support pending-message guards for empty SHA and non-PR scopes."""
        request_status = Mock(return_value={"pullRequest": {}})
        deps = codacy_zero_support.CodacyPendingMessageDeps(
            request_status=request_status,
            repository_analysis_url=Mock(return_value="repository-url"),
            pull_request_analysis_url=Mock(return_value="pull-request-url"),
            text_deps=codacy_zero_support.CodacyTextDeps(
                mapping_or_empty=check_codacy_zero._mapping_or_empty,
                preferred_text=check_codacy_zero._preferred_text,
            ),
        )
        self.assertIsNone(
            codacy_zero_support.analysis_pending_message(
                self._base_query(pull_request="5", sha=""),
                "token",
                deps=deps,
            )
        )
        request_status.assert_not_called()

        self.assertEqual(
            codacy_zero_support.analysis_pending_message(
                self._base_query(sha="targetsha"),
                "token",
                deps=deps,
            ),
            "Codacy analysis for repository is not available yet.",
        )
        request_status.assert_called_once_with("repository-url", "token")
        request_status.reset_mock()

        self.assertEqual(
            codacy_zero_support.analysis_pending_message(
                self._base_query(pull_request="5", sha="targetsha"),
                "token",
                deps=deps,
            ),
            "Codacy analysis for pull request 5 is not available yet.",
        )
        request_status.assert_called_once_with("pull-request-url", "token")

    def test_pull_request_issue_pending_message_skips_empty_sha_records(self) -> None:
        """Ignore issue records that do not yet carry a commit SHA."""
        sha_wait_message = Mock(return_value="waiting on commit")
        pending_message = codacy_zero_support.pull_request_issue_pending_message(
            {
                "analyzed": True,
                "data": [
                    {"commitIssue": {"commitInfo": {}}},
                    {"commitIssue": {"commitInfo": {"sha": "oldsha"}}},
                ],
            },
            self._base_query(pull_request="5"),
            "targetsha",
            deps=codacy_zero_support.CodacyIssuePendingDeps(
                text_deps=codacy_zero_support.CodacyTextDeps(
                    mapping_or_empty=check_codacy_zero._mapping_or_empty,
                    preferred_text=check_codacy_zero._preferred_text,
                ),
                sha_wait_message=sha_wait_message,
            ),
        )
        self.assertEqual(pending_message, "waiting on commit")
        sha_wait_message.assert_called_once_with(
            "pull request 5 issues",
            "oldsha",
            "targetsha",
        )

    def test_pull_request_issue_pending_message_returns_none_without_sha(self) -> None:
        """Return ``None`` when issue records never expose a commit SHA."""
        self.assertIsNone(
            codacy_zero_support.pull_request_issue_pending_message(
                {
                    "analyzed": True,
                    "data": [
                        {"commitIssue": {"commitInfo": {}}},
                        {"commitIssue": {"commitInfo": {"sha": ""}}},
                    ],
                },
                self._base_query(pull_request="5"),
                "targetsha",
                deps=codacy_zero_support.CodacyIssuePendingDeps(
                    text_deps=codacy_zero_support.CodacyTextDeps(
                        mapping_or_empty=check_codacy_zero._mapping_or_empty,
                        preferred_text=check_codacy_zero._preferred_text,
                    ),
                    sha_wait_message=Mock(return_value="unused"),
                ),
            )
        )

    def test_query_candidate_handles_success_and_provider_failure(self) -> None:
        """Cover successful and failed provider candidate queries."""
        query = self._base_query()
        with patch.object(
            check_codacy_zero,
            "_query_codacy_provider",
            return_value=(0, []),
        ):
            self.assertEqual(_query_codacy_candidate(query, "token"), (0, [], None, True))
        with patch.object(
            check_codacy_zero,
            "_query_codacy_provider",
            side_effect=RuntimeError("provider broke"),
        ):
            open_issues, findings, exc, should_return = _query_codacy_candidate(
                query, "token"
            )
        self.assertIsNone(open_issues)
        self.assertEqual(findings, ["Codacy API request failed: provider broke"])
        self.assertIsInstance(exc, RuntimeError)
        self.assertTrue(should_return)

    def test_not_found_findings_helpers(self) -> None:
        """Cover not-found findings with and without the last exception."""
        open_issues, findings, exc = check_codacy_zero._not_found_findings(
            ["gh"],
            RuntimeError("boom"),
        )
        self.assertIsNone(open_issues)
        self.assertEqual(
            findings,
            [
                "Codacy API endpoint was not found for providers: gh.",
                "Last Codacy API error: boom",
            ],
        )
        self.assertIsInstance(exc, RuntimeError)
        open_issues, findings, exc = check_codacy_zero._not_found_findings(["gh"], None)
        self.assertIsNone(open_issues)
        self.assertEqual(
            findings, ["Codacy API endpoint was not found for providers: gh."]
        )
        self.assertIsNone(exc)

    def test_main_status_requires_token(self) -> None:
        """Cover the missing-token main path."""
        args = Namespace(
            provider="gh",
            owner="Prekzursil",
            repo="quality-zero-platform",
            pull_request="",
            token="",
            out_json="codacy-zero/codacy.json",
            out_md="codacy-zero/codacy.md",
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(check_codacy_zero, "_parse_args", return_value=args),
            patch.object(check_codacy_zero, "write_report", return_value=0) as write_mock,
        ):
            self.assertEqual(check_codacy_zero.main(), 1)
        self.assertEqual(
            write_mock.call_args.args[0]["findings"],
            ["CODACY_API_TOKEN is missing."],
        )

    def test_main_status_passes_with_zero_issues(self) -> None:
        """Cover the successful main path."""
        args = Namespace(
            provider="gh",
            owner="Prekzursil",
            repo="quality-zero-platform",
            pull_request="5",
            token="explicit-token",
            out_json="codacy-zero/codacy.json",
            out_md="codacy-zero/codacy.md",
        )
        with (
            patch.object(check_codacy_zero, "_parse_args", return_value=args),
            patch.object(
                check_codacy_zero,
                "_query_codacy_open_issues",
                return_value=(0, [], None),
            ),
            patch.object(check_codacy_zero, "write_report", return_value=0) as write_mock,
        ):
            self.assertEqual(check_codacy_zero.main(), 0)
        self.assertEqual(write_mock.call_args.args[0]["status"], "pass")

    def test_main_status_returns_write_report_failure(self) -> None:
        """Cover write-report failures from the main path."""
        args = Namespace(
            provider="gh",
            owner="Prekzursil",
            repo="quality-zero-platform",
            pull_request="5",
            token="explicit-token",
            out_json="codacy-zero/codacy.json",
            out_md="codacy-zero/codacy.md",
        )
        with (
            patch.object(check_codacy_zero, "_parse_args", return_value=args),
            patch.object(
                check_codacy_zero,
                "_query_codacy_open_issues",
                return_value=(0, [], None),
            ),
            patch.object(check_codacy_zero, "write_report", return_value=7),
        ):
            self.assertEqual(check_codacy_zero.main(), 7)

    def test_main_status_audit_mode_keeps_success(self) -> None:
        """Cover audit-mode success when findings remain."""
        args = Namespace(
            provider="gh",
            owner="Prekzursil",
            repo="quality-zero-platform",
            pull_request="5",
            token="explicit-token",
            policy_mode="audit",
            out_json="codacy-zero/codacy.json",
            out_md="codacy-zero/codacy.md",
        )
        with (
            patch.object(check_codacy_zero, "_parse_args", return_value=args),
            patch.object(
                check_codacy_zero,
                "_query_codacy_open_issues",
                return_value=(5, ["Codacy reports 5 open issues (expected 0)."], None),
            ),
            patch.object(check_codacy_zero, "write_report", return_value=0),
        ):
            self.assertEqual(check_codacy_zero.main(), 0)
