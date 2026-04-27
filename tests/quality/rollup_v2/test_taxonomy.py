"""Tests for taxonomy loader (per design §A.4.4)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.taxonomy import load_all_taxonomies, lookup


class TaxonomyTests(unittest.TestCase):
    def test_codacy_pylint_broad_except(self):
        self.assertEqual(lookup("Codacy", "Pylint_W0703"), "broad-except")

    def test_sonarcloud_broad_except(self):
        self.assertEqual(lookup("SonarCloud", "python:S1166"), "broad-except")

    def test_codeql_broad_except(self):
        self.assertEqual(lookup("CodeQL", "py/bare-except"), "broad-except")

    def test_unknown_rule_returns_none(self):
        self.assertIsNone(lookup("Codacy", "Pylint_NoSuchRule"))

    def test_unknown_provider_returns_none(self):
        self.assertIsNone(lookup("MysteryVendor", "anything"))

    def test_load_all_taxonomies_returns_all_seven(self):
        all_tax = load_all_taxonomies()
        for provider in ("Codacy", "SonarCloud", "DeepSource", "Semgrep",
                         "CodeQL", "QLTY", "DeepScan"):
            self.assertIn(provider, all_tax)


class UnmappedRulesCollectorTests(unittest.TestCase):
    def test_unmapped_rules_collector(self):
        from scripts.quality.rollup_v2.taxonomy import UnmappedRulesCollector
        collector = UnmappedRulesCollector()
        collector.record("Codacy", "Pylint_Unknown_1")
        collector.record("Codacy", "Pylint_Unknown_1")
        collector.record("SonarCloud", "python:Snew")
        entries = collector.as_list()
        self.assertEqual(len(entries), 2)
        codacy_entry = next(e for e in entries if e["provider"] == "Codacy")
        self.assertEqual(codacy_entry["count"], 2)

    def test_empty_collector_returns_empty_list(self):
        from scripts.quality.rollup_v2.taxonomy import UnmappedRulesCollector
        collector = UnmappedRulesCollector()
        self.assertEqual(collector.as_list(), [])

    def test_entries_sorted_by_provider_then_rule(self):
        from scripts.quality.rollup_v2.taxonomy import UnmappedRulesCollector
        collector = UnmappedRulesCollector()
        collector.record("SonarCloud", "python:Sz")
        collector.record("Codacy", "Pylint_Z")
        collector.record("Codacy", "Pylint_A")
        entries = collector.as_list()
        keys = [(e["provider"], e["rule_id"]) for e in entries]
        self.assertEqual(keys, [("Codacy", "Pylint_A"), ("Codacy", "Pylint_Z"), ("SonarCloud", "python:Sz")])


if __name__ == "__main__":
    unittest.main()
