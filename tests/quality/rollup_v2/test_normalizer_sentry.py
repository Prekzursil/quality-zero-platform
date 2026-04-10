"""Tests for Sentry normalizer (per §6.6)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers.sentry import SentryNormalizer

_FIXTURE = Path(__file__).parent / "fixtures" / "normalizers" / "sentry_sample.json"


class SentryNormalizerTests(unittest.TestCase):
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
        result = SentryNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(len(result.findings), 2)

    def test_category_is_runtime_error(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = SentryNormalizer().run(artifact=artifact, repo_root=self.root)
        for finding in result.findings:
            self.assertEqual(finding.category, "runtime-error")

    def test_level_to_severity_mapping(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = SentryNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].severity, "high")    # error
        self.assertEqual(result.findings[1].severity, "medium")  # warning

    def test_metadata_filename_extraction(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = SentryNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].file, "scripts/quality/coverage_parsers.py")
        self.assertEqual(result.findings[1].file, "scripts/quality/common.py")


if __name__ == "__main__":
    unittest.main()
