"""Tests for BaseNormalizer abstract class (per design §B.1.2 + §A.6)."""
from __future__ import absolute_import

import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer, NormalizerResult
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
)


class _DemoNormalizer(BaseNormalizer):
    provider = "Codacy"

    def parse(self, artifact, repo_root):
        # Pretend to parse an artifact and yield one Finding
        return [
            self._build_finding(
                finding_id="demo-1",
                file="a.py",
                line=1,
                category="broad-except",
                category_group=CATEGORY_GROUP_QUALITY,
                severity="medium",
                primary_message='FOO_KEY = "sk-thirtytwocharsecretvalue" was here',
                rule_id="Pylint_W0703",
                rule_url=None,
                original_message="broad-except",
                context_snippet='API_KEY = "sk-thirtytwocharsecretvalue"',
            )
        ]


class BaseNormalizerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "a.py").write_text("pass\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_finalize_redacts_context_snippet(self):
        norm = _DemoNormalizer()
        result = norm.run(artifact=None, repo_root=self.root)
        self.assertIsInstance(result, NormalizerResult)
        self.assertEqual(len(result.findings), 1)
        finding = result.findings[0]
        self.assertNotIn("sk-thirtytwocharsecretvalue", finding.context_snippet)
        self.assertIn("<REDACTED>", finding.context_snippet)

    def test_finalize_redacts_primary_message(self):
        norm = _DemoNormalizer()
        result = norm.run(artifact=None, repo_root=self.root)
        self.assertNotIn("sk-thirtytwocharsecretvalue", result.findings[0].primary_message)

    def test_path_escape_produces_security_drop_not_finding(self):
        class _EscapeNormalizer(_DemoNormalizer):
            def parse(self, artifact, repo_root):
                return [
                    self._build_finding(
                        finding_id="x",
                        file="../../etc/passwd",
                        line=1,
                        category="broad-except",
                        category_group=CATEGORY_GROUP_QUALITY,
                        severity="low",
                        primary_message="m",
                        rule_id="R",
                        rule_url=None,
                        original_message="m",
                        context_snippet="",
                    )
                ]
        norm = _EscapeNormalizer()
        result = norm.run(artifact=None, repo_root=self.root)
        self.assertEqual(len(result.findings), 0)
        self.assertEqual(len(result.security_drops), 1)

    def test_crash_in_parse_is_caught_and_reported(self):
        class _CrashNormalizer(_DemoNormalizer):
            def parse(self, artifact, repo_root):
                raise ValueError("simulated parser crash")
        norm = _CrashNormalizer()
        result = norm.run(artifact=None, repo_root=self.root)
        self.assertEqual(len(result.findings), 0)
        self.assertEqual(len(result.normalizer_errors), 1)
        err = result.normalizer_errors[0]
        self.assertEqual(err["provider"], "Codacy")
        self.assertIn("simulated parser crash", err["error_message"])


if __name__ == "__main__":
    unittest.main()
