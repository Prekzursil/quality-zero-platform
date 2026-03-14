from __future__ import annotations

import unittest

from scripts.quality.check_codacy_zero import build_issues_url


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


if __name__ == "__main__":
    unittest.main()
