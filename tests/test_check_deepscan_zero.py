from __future__ import annotations

import unittest
from argparse import Namespace
from unittest.mock import patch

from scripts.quality import check_deepscan_zero


class DeepScanZeroTests(unittest.TestCase):
    def test_github_check_context_mode_passes_when_deepscan_status_is_green(self) -> None:
        args = Namespace(repo="Prekzursil/quality-zero-platform", sha="abc123")

        with patch.object(
            check_deepscan_zero,
            "_github_status_payload",
            return_value={"statuses": [{"context": "DeepScan", "state": "success", "target_url": "https://deepscan.io"}]},
        ):
            open_issues, source_url, findings = check_deepscan_zero._evaluate_github_check_context(args, token="token")

        self.assertEqual(open_issues, 0)
        self.assertEqual(source_url, "https://deepscan.io")
        self.assertEqual(findings, [])

    def test_github_check_context_mode_fails_when_deepscan_status_is_missing(self) -> None:
        args = Namespace(repo="Prekzursil/quality-zero-platform", sha="abc123")

        with patch.object(check_deepscan_zero, "_github_status_payload", return_value={"statuses": []}):
            open_issues, source_url, findings = check_deepscan_zero._evaluate_github_check_context(args, token="token")

        self.assertIsNone(open_issues)
        self.assertEqual(source_url, "")
        self.assertIn("DeepScan GitHub status context is missing.", findings)

    def test_validate_deepscan_inputs_accepts_github_check_context_mode(self) -> None:
        findings = check_deepscan_zero._validate_deepscan_inputs(
            token="token",
            policy_mode="github_check_context",
            open_issues_url="",
            github_token="ghs_123",
            repo="Prekzursil/quality-zero-platform",
            sha="abc123",
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
