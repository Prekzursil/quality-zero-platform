"""Tests for Codacy normalizer (per §6.1)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers.codacy import CodacyNormalizer

_FIXTURE = Path(__file__).parent / "fixtures" / "normalizers" / "codacy_sample.json"


class CodacyNormalizerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "scripts" / "quality").mkdir(parents=True)
        (self.root / "scripts" / "quality" / "coverage_parsers.py").write_text("pass", "utf-8")
        (self.root / "scripts" / "quality" / "common.py").write_text("pass", "utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_parses_two_findings(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CodacyNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(len(result.findings), 2)
        self.assertEqual(result.findings[0].category, "broad-except")
        self.assertEqual(result.findings[1].category, "unused-import")

    def test_warning_maps_to_medium(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CodacyNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].severity, "medium")

    def test_info_maps_to_low(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CodacyNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[1].severity, "low")

    def test_unmapped_rule_falls_through_to_uncategorized(self):
        artifact = {
            "issues": [{
                "message": "Unknown rule fires",
                "patternId": "Pylint_NoSuchRule",
                "patternUrl": None,
                "filename": "scripts/quality/common.py",
                "line": 1,
                "severity": "Info",
            }]
        }
        result = CodacyNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].category, "uncategorized")


if __name__ == "__main__":
    unittest.main()
