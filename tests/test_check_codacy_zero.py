"""Test check codacy zero."""

from __future__ import absolute_import

import unittest
from unittest.mock import patch

import scripts.quality.check_codacy_zero as check_codacy_zero
from scripts.quality.check_codacy_zero import (
    CodacyQuery,
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
