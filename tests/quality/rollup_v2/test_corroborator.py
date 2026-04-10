"""Tests for Corroborator dataclass (per design §A.3.2 + §B.3.4)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.providers import UNKNOWN_PROVIDER_RANK
from scripts.quality.rollup_v2.types.corroborator import Corroborator


class CorroboratorTests(unittest.TestCase):
    def test_from_provider_populates_rank(self):
        c = Corroborator.from_provider(
            provider="SonarCloud",
            rule_id="python:S1166",
            rule_url="https://sonarcloud.io/S1166",
            original_message="Catch a more specific exception",
        )
        self.assertEqual(c.provider, "SonarCloud")
        self.assertEqual(c.rule_id, "python:S1166")
        self.assertEqual(c.provider_priority_rank, 1)

    def test_from_provider_unknown_provider_uses_sentinel_rank(self):
        c = Corroborator.from_provider(
            provider="MysteryVendor",
            rule_id="X-001",
            rule_url=None,
            original_message="msg",
        )
        self.assertEqual(c.provider_priority_rank, UNKNOWN_PROVIDER_RANK)

    def test_frozen(self):
        c = Corroborator.from_provider("SonarCloud", "S1", None, "m")
        with self.assertRaises(Exception):
            c.provider = "Other"  # type: ignore[misc]

    def test_direct_construction_with_unmapped_rank_raises(self):
        with self.assertRaises(AssertionError):
            Corroborator(
                provider="SonarCloud",
                rule_id="S1",
                rule_url=None,
                original_message="m",
                provider_priority_rank=-1,
            )


if __name__ == "__main__":
    unittest.main()
