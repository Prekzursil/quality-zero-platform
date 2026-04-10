"""Tests for CodeQL normalizer (per §9.2)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers.codeql import CodeQLNormalizer

_FIXTURE = Path(__file__).parent / "fixtures" / "normalizers" / "codeql_sample.sarif.json"


class CodeQLNormalizerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "db.py").write_text("pass", "utf-8")
        (self.root / "src" / "utils.py").write_text("pass", "utf-8")
        (self.root / "src" / "api.py").write_text("pass", "utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_parses_two_findings_from_fixture(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CodeQLNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(len(result.findings), 2)
        self.assertEqual(len(result.normalizer_errors), 0)

    def test_first_finding_maps_to_sql_injection(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CodeQLNormalizer().run(artifact=artifact, repo_root=self.root)
        f = result.findings[0]
        self.assertEqual(f.category, "sql-injection")
        self.assertEqual(f.severity, "high")
        self.assertEqual(f.file, "src/db.py")
        self.assertEqual(f.line, 23)
        self.assertEqual(f.end_line, 25)
        self.assertEqual(f.category_group, "security")

    def test_second_finding_maps_to_broad_except(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CodeQLNormalizer().run(artifact=artifact, repo_root=self.root)
        f = result.findings[1]
        self.assertEqual(f.category, "broad-except")
        self.assertEqual(f.severity, "medium")
        self.assertEqual(f.file, "src/utils.py")
        self.assertEqual(f.line, 50)

    def test_cwe_extracted(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CodeQLNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].cwe, "CWE-89")

    def test_rule_url_from_help_uri(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CodeQLNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(
            result.findings[0].corroborators[0].rule_url,
            "https://codeql.github.com/codeql-query-help/python/py-sql-injection/",
        )

    def test_string_input_accepted(self):
        raw = _FIXTURE.read_text("utf-8")
        result = CodeQLNormalizer().run(artifact=raw, repo_root=self.root)
        self.assertEqual(len(result.findings), 2)

    def test_bytes_input_accepted(self):
        raw = _FIXTURE.read_bytes()
        result = CodeQLNormalizer().run(artifact=raw, repo_root=self.root)
        self.assertEqual(len(result.findings), 2)

    def test_non_dict_non_str_returns_empty(self):
        result = CodeQLNormalizer().run(artifact=42, repo_root=self.root)
        self.assertEqual(len(result.findings), 0)

    def test_provider_is_codeql(self):
        self.assertEqual(CodeQLNormalizer().provider, "CodeQL")

    def test_codeql_properties_bag_cwe(self):
        """CodeQL uses properties.cwe in addition to tags."""
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CodeQLNormalizer().run(artifact=artifact, repo_root=self.root)
        # First result has properties.cwe = "CWE-89"
        self.assertEqual(result.findings[0].cwe, "CWE-89")

    def test_context_snippet_from_codeql(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CodeQLNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertIn("cursor", result.findings[0].context_snippet)


if __name__ == "__main__":
    unittest.main()
