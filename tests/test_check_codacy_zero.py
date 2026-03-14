from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.quality.check_codacy_zero import _query_codacy_open_issues, build_issues_url


class CodacyZeroTests(unittest.TestCase):
    def test_build_issues_url_uses_pull_request_endpoint_when_available(self) -> None:
        self.assertEqual(
            build_issues_url("gh", "Prekzursil", "quality-zero-platform", pull_request="5"),
            "https://app.codacy.com/api/v3/analysis/organizations/gh/Prekzursil/repositories/quality-zero-platform/pull-requests/5/issues?status=new&limit=1",
        )

    def test_build_issues_url_uses_repository_endpoint_without_pull_request(self) -> None:
        self.assertEqual(
            build_issues_url("gh", "Prekzursil", "quality-zero-platform", pull_request=""),
            "https://api.codacy.com/api/v3/analysis/organizations/gh/Prekzursil/repositories/quality-zero-platform/issues/search?limit=1",
        )

    def test_query_open_issues_falls_back_after_a_404_provider_probe(self) -> None:
        responses = [
            Exception("sentinel"),
            {"total": 0},
        ]

        def fake_request(url: str, token: str, *, method: str = "GET", data=None):
            current = responses.pop(0)
            if isinstance(current, Exception):
                from urllib.error import HTTPError

                raise HTTPError(url, 404, "Not Found", hdrs=None, fp=None)
            return current

        with patch("scripts.quality.check_codacy_zero._request_json", side_effect=fake_request):
            open_issues, findings, _ = _query_codacy_open_issues(
                "Prekzursil",
                "quality-zero-platform",
                "token",
                ["custom", "gh"],
            )

        self.assertEqual(open_issues, 0)
        self.assertEqual(findings, [])

    def test_query_open_issues_uses_pull_request_get_endpoint(self) -> None:
        captured: list[tuple[str, str, object | None]] = []

        def fake_request(url: str, token: str, *, method: str = "GET", data=None):
            captured.append((url, method, data))
            return {"total": 0}

        with patch("scripts.quality.check_codacy_zero._request_json", side_effect=fake_request):
            open_issues, findings, _ = _query_codacy_open_issues(
                "Prekzursil",
                "quality-zero-platform",
                "token",
                ["gh"],
                pull_request="5",
            )

        self.assertEqual(open_issues, 0)
        self.assertEqual(findings, [])
        self.assertEqual(
            captured,
            [
                (
                    "https://app.codacy.com/api/v3/analysis/organizations/gh/Prekzursil/repositories/quality-zero-platform/pull-requests/5/issues?status=new&limit=1",
                    "GET",
                    None,
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
