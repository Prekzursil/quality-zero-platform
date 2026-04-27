"""Tests for Semgrep normalizer (per §9.1)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers.semgrep import SemgrepNormalizer

_FIXTURE = Path(__file__).parent / "fixtures" / "normalizers" / "semgrep_sample.sarif.json"


class SemgrepNormalizerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "utils.py").write_text("pass", "utf-8")
        (self.root / "src" / "auth.py").write_text("pass", "utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_parses_two_findings_from_fixture(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = SemgrepNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(len(result.findings), 2)
        self.assertEqual(len(result.normalizer_errors), 0)

    def test_first_finding_maps_to_code_injection(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = SemgrepNormalizer().run(artifact=artifact, repo_root=self.root)
        f = result.findings[0]
        self.assertEqual(f.category, "code-injection")
        self.assertEqual(f.severity, "high")
        self.assertEqual(f.file, "src/utils.py")
        self.assertEqual(f.line, 15)
        self.assertEqual(f.category_group, "security")

    def test_second_finding_maps_to_weak_crypto(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = SemgrepNormalizer().run(artifact=artifact, repo_root=self.root)
        f = result.findings[1]
        self.assertEqual(f.category, "weak-crypto")
        self.assertEqual(f.severity, "medium")
        self.assertEqual(f.file, "src/auth.py")
        self.assertEqual(f.line, 42)

    def test_cwe_extracted_from_tags(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = SemgrepNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].cwe, "CWE-95")

    def test_rule_url_from_help_uri(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = SemgrepNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(
            result.findings[0].corroborators[0].rule_url,
            "https://semgrep.dev/r/python.lang.security.dangerous-eval",
        )

    def test_string_input_accepted(self):
        raw = _FIXTURE.read_text("utf-8")
        result = SemgrepNormalizer().run(artifact=raw, repo_root=self.root)
        self.assertEqual(len(result.findings), 2)

    def test_bytes_input_accepted(self):
        raw = _FIXTURE.read_bytes()
        result = SemgrepNormalizer().run(artifact=raw, repo_root=self.root)
        self.assertEqual(len(result.findings), 2)

    def test_non_dict_non_str_returns_empty(self):
        result = SemgrepNormalizer().run(artifact=12345, repo_root=self.root)
        self.assertEqual(len(result.findings), 0)

    def test_empty_sarif_returns_empty(self):
        result = SemgrepNormalizer().run(artifact={"runs": []}, repo_root=self.root)
        self.assertEqual(len(result.findings), 0)

    def test_provider_is_semgrep(self):
        self.assertEqual(SemgrepNormalizer().provider, "Semgrep")

    def test_context_snippet_preserved(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = SemgrepNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertIn("run_dynamic", result.findings[0].context_snippet)


if __name__ == "__main__":
    unittest.main()
