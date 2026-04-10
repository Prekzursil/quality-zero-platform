"""Tests for severity ordering (per design §A.4.2)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.severity import SEVERITY_ORDER, max_severity


class SeverityOrderTests(unittest.TestCase):
    def test_severity_order_tuple(self):
        self.assertEqual(
            SEVERITY_ORDER,
            ("critical", "high", "medium", "low", "info"),
        )

    def test_max_severity_picks_highest(self):
        self.assertEqual(max_severity(["low", "critical", "medium"]), "critical")

    def test_max_severity_single(self):
        self.assertEqual(max_severity(["medium"]), "medium")

    def test_max_severity_all_same(self):
        self.assertEqual(max_severity(["high", "high", "high"]), "high")

    def test_max_severity_empty_raises(self):
        with self.assertRaises(ValueError):
            max_severity([])

    def test_max_severity_unknown_severity_raises(self):
        with self.assertRaises(ValueError):
            max_severity(["bogus"])


if __name__ == "__main__":
    unittest.main()
