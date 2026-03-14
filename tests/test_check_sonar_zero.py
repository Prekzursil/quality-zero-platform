from __future__ import annotations

import argparse
import unittest

from scripts.quality.check_sonar_zero import load_sonar_findings_with_retry


class SonarZeroTests(unittest.TestCase):
    def test_retry_waits_for_pr_scoped_findings_to_settle(self) -> None:
        args = argparse.Namespace(branch="", pull_request="5")
        responses = [
            (1, "OK", ["Sonar reports 1 open issues (expected 0)."]),
            (0, "OK", []),
        ]
        attempts: list[int] = []

        def fake_loader(current_args, auth):
            attempts.append(len(attempts) + 1)
            return responses.pop(0)

        open_issues, quality_gate, findings = load_sonar_findings_with_retry(
            args,
            "auth",
            fetch_fn=fake_loader,
            attempts=2,
            sleep_seconds=0.0,
        )

        self.assertEqual((open_issues, quality_gate, findings), (0, "OK", []))
        self.assertEqual(attempts, [1, 2])

    def test_retry_skips_unscoped_queries(self) -> None:
        args = argparse.Namespace(branch="", pull_request="")
        attempts: list[int] = []

        def fake_loader(current_args, auth):
            attempts.append(len(attempts) + 1)
            return 3, "ERROR", ["Sonar reports 3 open issues (expected 0)."]

        open_issues, quality_gate, findings = load_sonar_findings_with_retry(
            args,
            "auth",
            fetch_fn=fake_loader,
            attempts=3,
            sleep_seconds=0.0,
        )

        self.assertEqual((open_issues, quality_gate, findings), (3, "ERROR", ["Sonar reports 3 open issues (expected 0)."]))
        self.assertEqual(attempts, [1])


if __name__ == "__main__":
    unittest.main()
