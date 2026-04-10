"""Tests for provider priority ranking (per design §A.4.3)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.providers import (
    PROVIDER_PRIORITY_RANK,
    UNKNOWN_PROVIDER_RANK,
    priority_rank_for,
)


class ProviderPriorityTests(unittest.TestCase):
    def test_codeql_has_highest_priority(self):
        self.assertEqual(PROVIDER_PRIORITY_RANK["CodeQL"], 0)

    def test_sonar_above_codacy(self):
        self.assertLess(
            PROVIDER_PRIORITY_RANK["SonarCloud"],
            PROVIDER_PRIORITY_RANK["Codacy"],
        )

    def test_ordering_matches_design_a_4_3(self):
        expected_order = (
            "CodeQL",
            "SonarCloud",
            "Codacy",
            "DeepSource",
            "Semgrep",
            "QLTY",
            "DeepScan",
        )
        for index, name in enumerate(expected_order):
            self.assertEqual(PROVIDER_PRIORITY_RANK[name], index)

    def test_non_analyzer_providers_rank_high(self):
        for non_analyzer in ("Sentry", "Chromatic", "Applitools"):
            self.assertEqual(PROVIDER_PRIORITY_RANK[non_analyzer], UNKNOWN_PROVIDER_RANK)

    def test_priority_rank_for_known(self):
        self.assertEqual(priority_rank_for("CodeQL"), 0)

    def test_priority_rank_for_unknown_returns_sentinel(self):
        self.assertEqual(priority_rank_for("MysteryVendor"), UNKNOWN_PROVIDER_RANK)


if __name__ == "__main__":
    unittest.main()
