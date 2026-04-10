"""Tests for QualitySecrets normalizer (per §6.9, special handling)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers.secrets import SecretsNormalizer

_FIXTURE = Path(__file__).parent / "fixtures" / "normalizers" / "secrets_sample.json"


class SecretsNormalizerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "scripts" / "quality").mkdir(parents=True)
        (self.root / "scripts" / "quality" / "common.py").write_text("pass", "utf-8")
        (self.root / "scripts" / "quality" / "coverage_parsers.py").write_text("pass", "utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_parses_two_findings(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = SecretsNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(len(result.findings), 2)

    def test_all_findings_are_critical_security(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = SecretsNormalizer().run(artifact=artifact, repo_root=self.root)
        for finding in result.findings:
            self.assertEqual(finding.severity, "critical")
            self.assertEqual(finding.category_group, "security")
            self.assertEqual(finding.category, "hardcoded-secret")

    def test_cwe_798(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = SecretsNormalizer().run(artifact=artifact, repo_root=self.root)
        for finding in result.findings:
            self.assertEqual(finding.cwe, "CWE-798")

    def test_provider_is_quality_secrets(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = SecretsNormalizer().run(artifact=artifact, repo_root=self.root)
        for finding in result.findings:
            self.assertEqual(finding.corroborators[0].provider, "QualitySecrets")


if __name__ == "__main__":
    unittest.main()
