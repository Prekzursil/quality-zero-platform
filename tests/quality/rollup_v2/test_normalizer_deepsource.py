"""Tests for DeepSource normalizer (per §6.3)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers.deepsource import DeepSourceNormalizer

_FIXTURE = Path(__file__).parent / "fixtures" / "normalizers" / "deepsource_sample.json"


class DeepSourceNormalizerTests(unittest.TestCase):
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
        result = DeepSourceNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(len(result.findings), 3)

    def test_mapped_category(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = DeepSourceNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].category, "broad-except")
        self.assertEqual(result.findings[1].category, "unused-import")

    def test_unmapped_rule_to_uncategorized(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = DeepSourceNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[2].category, "uncategorized")

    def test_severity_mapping(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = DeepSourceNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].severity, "medium")  # MAJOR
        self.assertEqual(result.findings[1].severity, "low")     # MINOR
        self.assertEqual(result.findings[2].severity, "high")    # CRITICAL


if __name__ == "__main__":
    unittest.main()
