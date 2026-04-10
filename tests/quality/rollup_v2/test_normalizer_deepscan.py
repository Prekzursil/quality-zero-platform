"""Tests for DeepScan normalizer (per §6.4)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers.deepscan import DeepScanNormalizer

_FIXTURE = Path(__file__).parent / "fixtures" / "normalizers" / "deepscan_sample.json"


class DeepScanNormalizerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "scripts" / "quality").mkdir(parents=True)
        (self.root / "scripts" / "quality" / "coverage_parsers.py").write_text("pass", "utf-8")
        (self.root / "scripts" / "quality" / "common.py").write_text("pass", "utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_parses_three_findings(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = DeepScanNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(len(result.findings), 3)

    def test_mapped_category(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = DeepScanNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].category, "unused-variable")
        self.assertEqual(result.findings[1].category, "dead-code")

    def test_unmapped_rule_to_uncategorized(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = DeepScanNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[2].category, "uncategorized")

    def test_all_severities_are_medium(self):
        """DeepScan has no per-alarm severity; all default to medium."""
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = DeepScanNormalizer().run(artifact=artifact, repo_root=self.root)
        for finding in result.findings:
            self.assertEqual(finding.severity, "medium")


if __name__ == "__main__":
    unittest.main()
