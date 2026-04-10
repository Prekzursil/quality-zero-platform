"""Tests for QLTY normalizer (per §6.5)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers.qlty import QLTYNormalizer

_FIXTURE = Path(__file__).parent / "fixtures" / "normalizers" / "qlty_sample.json"


class QLTYNormalizerTests(unittest.TestCase):
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
        result = QLTYNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(len(result.findings), 3)

    def test_mapped_category(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = QLTYNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].category, "unused-import")
        self.assertEqual(result.findings[1].category, "too-long")

    def test_unmapped_rule_to_uncategorized(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = QLTYNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[2].category, "uncategorized")

    def test_severity_passthrough(self):
        """QLTY provides severity directly; normalizer passes it through."""
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = QLTYNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].severity, "high")
        self.assertEqual(result.findings[1].severity, "medium")
        self.assertEqual(result.findings[2].severity, "low")


if __name__ == "__main__":
    unittest.main()
