"""Tests for Dependabot normalizer (per §6.7)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers.dependabot import DependabotNormalizer

_FIXTURE = Path(__file__).parent / "fixtures" / "normalizers" / "dependabot_sample.json"


class DependabotNormalizerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "requirements.txt").write_text("foo-package==1.0.0\n", "utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_parses_three_findings(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = DependabotNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(len(result.findings), 3)

    def test_all_findings_are_security(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = DependabotNormalizer().run(artifact=artifact, repo_root=self.root)
        for finding in result.findings:
            self.assertEqual(finding.category_group, "security")
            self.assertEqual(finding.category, "vulnerable-dependency")

    def test_severity_mapping(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = DependabotNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].severity, "critical")
        self.assertEqual(result.findings[1].severity, "high")
        self.assertEqual(result.findings[2].severity, "low")

    def test_cwe_extraction(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = DependabotNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].cwe, "CWE-94")
        self.assertIsNone(result.findings[1].cwe)


if __name__ == "__main__":
    unittest.main()
