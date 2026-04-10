"""Tests for Coverage normalizer (per §6.8, special handling)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers.coverage import CoverageNormalizer

_FIXTURE = Path(__file__).parent / "fixtures" / "normalizers" / "coverage_sample.json"


class CoverageNormalizerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        # Coverage uses module names as file paths, not real files.
        # Create placeholder files so path validation passes.
        (self.root / "scripts-quality").write_text("pass", "utf-8")
        (self.root / "scripts-rollup").write_text("pass", "utf-8")
        (self.root / "scripts-utils").write_text("pass", "utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_only_below_100_percent_emit_findings(self):
        """100% coverage modules should NOT produce findings."""
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CoverageNormalizer().run(artifact=artifact, repo_root=self.root)
        # scripts-quality (75%), scripts-rollup (90%) emit; scripts-utils (100%) does not
        self.assertEqual(len(result.findings), 2)

    def test_category_is_coverage_gap(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CoverageNormalizer().run(artifact=artifact, repo_root=self.root)
        for finding in result.findings:
            self.assertEqual(finding.category, "coverage-gap")
            self.assertEqual(finding.category_group, "quality")

    def test_severity_from_percent(self):
        """<80 -> high, 80-95 -> medium, 95-99 -> low."""
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CoverageNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].severity, "high")    # 75%
        self.assertEqual(result.findings[1].severity, "medium")  # 90%

    def test_primary_message_contains_percent(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CoverageNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertIn("75.0%", result.findings[0].primary_message)


if __name__ == "__main__":
    unittest.main()
