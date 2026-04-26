"""Test check codacy zero — retry, fallback, and entrypoint edge cases."""

from __future__ import absolute_import

import unittest
from argparse import Namespace
from email.message import Message
from urllib.error import HTTPError
from unittest.mock import patch

import scripts.quality.check_codacy_zero as check_codacy_zero
from scripts.quality import codacy_zero_support
from unittest.mock import Mock
from scripts.quality.check_codacy_zero import (
    CodacyQuery,
    CodacyRetryConfig,
    _query_codacy_candidate,
    _query_codacy_open_issues,
)


def _raise_runtime_error(message: str) -> None:
    """Raise one runtime error for callback tests."""
    raise RuntimeError(message)


class CodacyZeroEdgeTests(unittest.TestCase):
    """Codacy Zero edge-case, retry, fallback, and entrypoint tests."""

    @staticmethod
    def _base_query(
        *,
        provider: str = "gh",
        pull_request: str = "",
        sha: str = "",
    ) -> CodacyQuery:
        """Handle base query."""
        return CodacyQuery(
            provider,
            "Prekzursil",
            "quality-zero-platform",
            pull_request=pull_request,
            sha=sha,
        )

    @staticmethod
    def _retry_config(
        provider_candidates,
        *,
        attempts: int = 1,
        pending_fn=check_codacy_zero._analysis_pending_message,
        sleep_seconds: float = 0.0,
    ) -> CodacyRetryConfig:
        """Handle retry config."""
        return CodacyRetryConfig(
            provider_candidates=tuple(provider_candidates),
            attempts=attempts,
            pending_fn=pending_fn,
            sleep_seconds=sleep_seconds,
        )

    def test_codacy_support_pending_message_handles_empty_sha_and_non_pr(self) -> None:
        """Cover support pending-message guards for empty sha and non-PR queries."""
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

    def test_pull_request_issue_pending_skips_empty_sha(self) -> None:
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

    def test_pull_request_issue_pending_returns_none_without_sha(self) -> None:
        """Return ``None`` when the issue records never expose a commit SHA."""
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

    def test_open_issue_query_paths(self) -> None:
        """Cover open issue query paths."""
        responses = [Exception("sentinel"), {"total": 0}]

        def fake_request(url: str, token: str, *, method: str = "GET", data=None):
            """Handle fake request."""
            current = responses.pop(0)
            if isinstance(current, Exception):
                raise HTTPError(url, 404, "Not Found", hdrs=Message(), fp=None)
            return current

        with patch(
            "scripts.quality.check_codacy_zero._request_json", side_effect=fake_request
        ):
            self.assertEqual(
                _query_codacy_open_issues(
                    self._base_query(), "token", ["custom", "gh"]
                ),
                (0, [], None),
            )

        captured = []

        def capture_request(url: str, token: str, *, method: str = "GET", data=None):
            """Handle capture request."""
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

    def test_open_issue_http_error_and_not_found_paths(self) -> None:
        """Cover open issue http error and not found paths."""
        error = HTTPError(
            "https://api.codacy.com", 401, "Unauthorized", hdrs=Message(), fp=None
        )
        with (
            patch(
                "scripts.quality.check_codacy_zero._query_codacy_provider",
                side_effect=error,
            ),
            patch(
                (
                    "scripts.quality.check_codacy_zero."
                    "_query_codacy_public_repository_issues"
                ),
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

        def fake_provider(*_args, **_kwargs):
            """Handle fake provider."""
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

    def test_fallback_and_http_error_helpers(self) -> None:
        """Cover fallback and http error helpers cover remaining branches."""
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

    def test_direct_wrapper_helpers(self) -> None:
        """Cover direct wrapper helpers delegate to support functions."""
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

    def test_query_candidate_and_helpers(self) -> None:
        """Cover query candidate and helpers."""
        query = self._base_query()
        with patch.object(
            check_codacy_zero,
            "_query_codacy_provider",
            return_value=(0, []),
        ):
            self.assertEqual(
                _query_codacy_candidate(query, "token"),
                (0, [], None, True),
            )
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

    def test_not_found_findings_without_exception(self) -> None:
        """Cover not found findings without exception."""
        open_issues, findings, exc = check_codacy_zero._not_found_findings(["gh"], None)
        self.assertIsNone(open_issues)
        self.assertEqual(
            findings, ["Codacy API endpoint was not found for providers: gh."]
        )
        self.assertIsNone(exc)

    def test_main_status_paths(self) -> None:
        """Cover main status paths."""
        empty_value = str()
        args = Namespace(
            provider="gh",
            owner="Prekzursil",
            repo="quality-zero-platform",
            pull_request=empty_value,
            token=empty_value,
            out_json="codacy-zero/codacy.json",
            out_md="codacy-zero/codacy.md",
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(check_codacy_zero, "_parse_args", return_value=args),
            patch.object(
                check_codacy_zero, "write_report", return_value=0
            ) as write_report_mock,
        ):
            self.assertEqual(check_codacy_zero.main(), 1)
        self.assertEqual(
            write_report_mock.call_args.args[0]["findings"],
            ["CODACY_API_TOKEN is missing."],
        )

        success_args = Namespace(
            **{**args.__dict__, "token": "explicit-token", "pull_request": "5"}
        )
        with (
            patch.object(check_codacy_zero, "_parse_args", return_value=success_args),
            patch.object(
                check_codacy_zero,
                "_query_codacy_open_issues",
                return_value=(0, [], None),
            ),
            patch.object(
                check_codacy_zero, "write_report", return_value=0
            ) as write_report_mock,
        ):
            self.assertEqual(check_codacy_zero.main(), 0)
        self.assertEqual(write_report_mock.call_args.args[0]["status"], "pass")

        with (
            patch.object(check_codacy_zero, "_parse_args", return_value=success_args),
            patch.object(
                check_codacy_zero,
                "_query_codacy_open_issues",
                return_value=(0, [], None),
            ),
            patch.object(check_codacy_zero, "write_report", return_value=7),
        ):
            self.assertEqual(check_codacy_zero.main(), 7)

        audit_args = Namespace(**{**success_args.__dict__, "policy_mode": "audit"})
        with (
            patch.object(check_codacy_zero, "_parse_args", return_value=audit_args),
            patch.object(
                check_codacy_zero,
                "_query_codacy_open_issues",
                return_value=(5, ["Codacy reports 5 open issues (expected 0)."], None),
            ),
            patch.object(check_codacy_zero, "write_report", return_value=0),
        ):
            self.assertEqual(check_codacy_zero.main(), 0)

