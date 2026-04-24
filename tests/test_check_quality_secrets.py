"""Tests for the Phase 5 secret-missing wiring in ``check_quality_secrets``.

This suite focuses on the new ``open_secret_missing_alerts`` helper added
to close the §9 secrets-sync criterion:

    ``check_quality_secrets.py`` fails when any severity:block scanner
    lacks its secret on the target repo; opens ``alert:secret-missing``.
"""

from __future__ import absolute_import

import subprocess
import unittest
from unittest.mock import MagicMock

from scripts.quality import check_quality_secrets as cqs


def _fake_completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    """Helper: ``CompletedProcess`` double for runner mocks."""
    return subprocess.CompletedProcess(
        args=["gh"], returncode=returncode, stdout=stdout, stderr="",
    )


class OpenSecretMissingAlertsTests(unittest.TestCase):
    """``open_secret_missing_alerts`` opens one issue per missing secret."""

    def test_no_missing_secrets_opens_nothing(self) -> None:
        """Empty ``missing_secrets`` → zero opener calls, zero results."""
        runner = MagicMock()
        results = cqs.open_secret_missing_alerts(
            platform_slug="Prekzursil/quality-zero-platform",
            target_repo_slug="Prekzursil/event-link",
            missing_secrets=[],
            runner=runner,
        )
        self.assertEqual(results, [])
        runner.assert_not_called()

    def test_dry_run_yields_stub_records_without_calling_gh(self) -> None:
        """``dry_run=True`` never calls the runner."""
        runner = MagicMock()
        results = cqs.open_secret_missing_alerts(
            platform_slug="Prekzursil/quality-zero-platform",
            target_repo_slug="Prekzursil/event-link",
            missing_secrets=["SONAR_TOKEN", "CODECOV_TOKEN"],
            runner=runner,
            dry_run=True,
        )
        runner.assert_not_called()
        self.assertEqual(len(results), 2)
        for record in results:
            self.assertFalse(record["created"])

    def test_real_call_fires_one_opener_per_missing_secret(self) -> None:
        """Two missing secrets → ``gh issue list`` + create called for each."""
        # 4 responses: (list, create) for secret #1 + (list, create) for #2
        responses = [
            _fake_completed("[]"),  # no existing issue
            _fake_completed(
                "https://github.com/Prekzursil/quality-zero-platform/issues/501\n",
            ),
            _fake_completed("[]"),
            _fake_completed(
                "https://github.com/Prekzursil/quality-zero-platform/issues/502\n",
            ),
        ]
        runner = MagicMock(side_effect=responses)
        results = cqs.open_secret_missing_alerts(
            platform_slug="Prekzursil/quality-zero-platform",
            target_repo_slug="Prekzursil/event-link",
            missing_secrets=["SONAR_TOKEN", "CODECOV_TOKEN"],
            runner=runner,
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(runner.call_count, 4)
        created = sorted(r["title"] for r in results)
        self.assertIn("[alert:secret-missing] Prekzursil/event-link:SONAR_TOKEN", created)
        self.assertIn("[alert:secret-missing] Prekzursil/event-link:CODECOV_TOKEN", created)

    def test_blank_secrets_filtered_out_by_detector(self) -> None:
        """Blank entries in ``missing_secrets`` don't produce alerts."""
        runner = MagicMock()
        results = cqs.open_secret_missing_alerts(
            platform_slug="Prekzursil/quality-zero-platform",
            target_repo_slug="Prekzursil/event-link",
            missing_secrets=["", "  ", ""],
            runner=runner,
            dry_run=True,
        )
        self.assertEqual(results, [])
        runner.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
