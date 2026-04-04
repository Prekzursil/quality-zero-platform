"""Test check codacy zero — core URL, query, and pending-message paths."""

from __future__ import absolute_import

import unittest
from typing import List, Tuple
from unittest.mock import patch

import scripts.quality.check_codacy_zero as check_codacy_zero
from scripts.quality.check_codacy_zero import (
    CodacyQuery,
    CodacyRetryConfig,
    _build_retry_config,
    _query_codacy_provider,
    _request_mode,
    build_issues_url,
    build_pull_request_analysis_url,
    build_repository_analysis_url,
    extract_total_open,
)


class CodacyZeroTests(unittest.TestCase):
    """Codacy Zero Tests."""

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
        provider_candidates: Tuple[str, ...],
        *,
        attempts: int = 1,
        pending_fn=check_codacy_zero._analysis_pending_message,
        sleep_seconds: float = 0.0,
    ) -> CodacyRetryConfig:
        """Handle retry config."""
        return CodacyRetryConfig(
            provider_candidates=provider_candidates,
            attempts=attempts,
            pending_fn=pending_fn,
            sleep_seconds=sleep_seconds,
        )

    def test_build_urls_and_request_mode(self) -> None:
        """Cover build urls and request mode."""
        analysis_base_url = (
            "https://app.codacy.com/api/v3/analysis/organizations/gh/"
            "Prekzursil/repositories/quality-zero-platform"
        )
        issues_base_url = (
            "https://api.codacy.com/api/v3/analysis/organizations/gh/"
            "Prekzursil/repositories/quality-zero-platform"
        )
        self.assertEqual(_request_mode(self._base_query()), ("POST", {}))
        self.assertEqual(
            _request_mode(self._base_query(sha="abc123")),
            ("POST", {"commitUuid": "abc123"}),
        )
        self.assertEqual(
            _request_mode(self._base_query(pull_request="5")), ("GET", None)
        )
        self.assertEqual(
            build_issues_url(
                "gh", "Prekzursil", "quality-zero-platform", pull_request=""
            ),
            f"{issues_base_url}/issues/search?limit=1",
        )
        self.assertEqual(
            build_issues_url(
                "gh", "Prekzursil", "quality-zero-platform", pull_request="5"
            ),
            f"{analysis_base_url}/pull-requests/5/issues?status=new&limit=1",
        )
        self.assertEqual(
            build_repository_analysis_url(
                "gh", "Prekzursil", "quality-zero-platform"
            ),
            analysis_base_url,
        )
        self.assertEqual(
            build_pull_request_analysis_url(
                "gh", "Prekzursil", "quality-zero-platform", "5"
            ),
            f"{analysis_base_url}/pull-requests/5",
        )

    def test_extract_total_open_nested(self) -> None:
        """Cover extract total open nested."""
        self.assertEqual(extract_total_open({"issuesCount": 4}), 4)
        self.assertEqual(extract_total_open({"paging": {"total": 3}}), 3)
        self.assertEqual(extract_total_open([{"details": {"open_issues": 2}}]), 2)
        self.assertIsNone(extract_total_open({"items": [{"details": "no-count"}]}))

    def test_request_json_rejects_non_dict_payloads(self) -> None:
        """Cover request json rejects non dict payloads."""
        with (
            patch(
                "scripts.quality.check_codacy_zero.load_json_https",
                return_value=(["invalid"], {}),
            ),
            self.assertRaisesRegex(
                RuntimeError, "Unexpected Codacy API response payload"
            ),
        ):
            check_codacy_zero._request_json("https://api.codacy.com/test", "token")

        with patch(
            "scripts.quality.check_codacy_zero.load_json_https",
            return_value=({"total": 0}, {}),
        ):
            self.assertEqual(
                check_codacy_zero._request_json("https://api.codacy.com/test", "token"),
                {"total": 0},
            )

    def test_public_repository_issue_query_paths(self) -> None:
        """Cover public repository issue query paths."""
        with patch(
            "scripts.quality.check_codacy_zero.load_json_https",
            return_value=({"issuesCount": 0}, {}),
        ):
            self.assertEqual(
                check_codacy_zero._query_codacy_public_repository_issues(
                    "gh", "Prekzursil", "quality-zero-platform"
                ),
                (0, []),
            )
        with (
            patch(
                "scripts.quality.check_codacy_zero.load_json_https",
                return_value=("bad", {}),
            ),
            self.assertRaisesRegex(
                RuntimeError, "Unexpected Codacy public repository payload"
            ),
        ):
            check_codacy_zero._query_codacy_public_repository_issues(
                "gh", "Prekzursil", "quality-zero-platform"
            )
        with patch(
            "scripts.quality.check_codacy_zero.load_json_https",
            return_value=({"items": []}, {}),
        ):
            self.assertEqual(
                check_codacy_zero._query_codacy_public_repository_issues(
                    "gh", "Prekzursil", "quality-zero-platform"
                ),
                (
                    None,
                    ["Codacy response did not include a parseable total issue count."],
                ),
            )
        with patch(
            "scripts.quality.check_codacy_zero.load_json_https",
            return_value=({"issuesCount": 4}, {}),
        ):
            self.assertEqual(
                check_codacy_zero._query_codacy_public_repository_issues(
                    "gh", "Prekzursil", "quality-zero-platform"
                ),
                (4, ["Codacy reports 4 open issues (expected 0)."]),
            )

    def test_provider_query_paths(self) -> None:
        """Cover provider query paths."""
        with patch(
            "scripts.quality.check_codacy_zero._request_json",
            return_value={"items": []},
        ):
            self.assertEqual(
                _query_codacy_provider(self._base_query(), "token"),
                (
                    None,
                    ["Codacy response did not include a parseable total issue count."],
                ),
            )
        with patch(
            "scripts.quality.check_codacy_zero._request_json", return_value={"total": 2}
        ):
            self.assertEqual(
                _query_codacy_provider(self._base_query(), "token"),
                (2, ["Codacy reports 2 open issues (expected 0)."]),
            )

        captured: List[Tuple[str, str, object | None]] = []

        def capture_request(url: str, token: str, *, method: str = "GET", data=None):
            """Handle capture request."""
            captured.append((url, method, data))
            return {"total": 0}

        with patch(
            "scripts.quality.check_codacy_zero._request_json",
            side_effect=capture_request,
        ):
            self.assertEqual(
                _query_codacy_provider(self._base_query(sha="abc123"), "token"),
                (0, []),
            )

        self.assertEqual(
            captured,
            [
                (
                    "https://api.codacy.com/api/v3/analysis/organizations/gh/"
                    "Prekzursil/repositories/quality-zero-platform/"
                    "issues/search?limit=1",
                    "POST",
                    {"commitUuid": "abc123"},
                )
            ],
        )

    def test_build_retry_config_for_scoped_and_unscoped_queries(self) -> None:
        """Cover build retry config for scoped and unscoped queries."""
        unscoped = _build_retry_config(self._base_query(), ["gh"])
        self.assertEqual(unscoped.attempts, 1)
        self.assertEqual(unscoped.provider_candidates, ("gh",))
        scoped = _build_retry_config(
            CodacyQuery(
                "gh",
                "Prekzursil",
                "quality-zero-platform",
                pull_request="5",
                sha="targetsha",
            ),
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

    def test_analysis_pending_message_tracks_pull_request_state(self) -> None:
        """Cover analysis pending message tracks pull request state."""
        pr_query = CodacyQuery(
            "gh",
            "Prekzursil",
            "quality-zero-platform",
            pull_request="5",
            sha="targetsha",
        )
        with patch.object(
            check_codacy_zero,
            "_request_analysis_status",
            return_value={"isAnalysing": True},
        ):
            self.assertEqual(
                check_codacy_zero._analysis_pending_message(pr_query, "token"),
                "Codacy is still analysing pull request 5.",
            )
        with patch.object(
            check_codacy_zero,
            "_request_analysis_status",
            return_value={"pullRequest": {}},
        ):
            self.assertEqual(
                check_codacy_zero._analysis_pending_message(pr_query, "token"),
                "Codacy analysis for pull request 5 is not available yet.",
            )
        with patch.object(
            check_codacy_zero,
            "_request_analysis_status",
            return_value={"pullRequest": {"headCommitSha": "oldsha"}},
        ):
            self.assertEqual(
                check_codacy_zero._analysis_pending_message(pr_query, "token"),
                (
                    "Codacy analysis for pull request 5 is still on oldsha "
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
        """Cover analysis pending message tracks repository state."""
        repo_query = CodacyQuery(
            "gh", "Prekzursil", "quality-zero-platform", sha="targetsha"
        )
        with patch.object(
            check_codacy_zero, "_request_analysis_status", return_value={"data": {}}
        ):
            self.assertEqual(
                check_codacy_zero._analysis_pending_message(repo_query, "token"),
                "Codacy analysis for repository is not available yet.",
            )
        with patch.object(
            check_codacy_zero,
            "_request_analysis_status",
            return_value={"data": {"lastAnalysedCommit": {"sha": "oldsha"}}},
        ):
            self.assertEqual(
                check_codacy_zero._analysis_pending_message(repo_query, "token"),
                (
                    "Codacy analysis for repository is still on oldsha "
                    "(waiting for targetsha)."
                ),
            )
        with patch.object(
            check_codacy_zero,
            "_request_analysis_status",
            return_value={"data": {"lastAnalysedCommit": {"sha": "targetsha"}}},
        ):
            self.assertEqual(
                check_codacy_zero._analysis_pending_message(repo_query, "token"),
                "Codacy repository analysis has not finished yet.",
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


