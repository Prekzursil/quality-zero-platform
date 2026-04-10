"""Tests for Chromatic normalizer (per §9.3)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers.chromatic import ChromaticNormalizer

_FIXTURE = Path(__file__).parent / "fixtures" / "normalizers" / "chromatic_sample.json"


class ChromaticNormalizerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def test_parses_two_change_findings(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = ChromaticNormalizer().run(artifact=artifact, repo_root=self.root)
        # Fixture has 2 CHANGED items, 0 errors -> 2 findings
        self.assertEqual(len(result.findings), 2)
        self.assertEqual(len(result.normalizer_errors), 0)

    def test_change_finding_severity_is_medium(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = ChromaticNormalizer().run(artifact=artifact, repo_root=self.root)
        for f in result.findings:
            self.assertEqual(f.severity, "medium")

    def test_change_finding_category(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = ChromaticNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].category, "visual-regression-diff")
        self.assertEqual(result.findings[0].category_group, "quality")

    def test_finding_message_includes_component(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = ChromaticNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertIn("Button", result.findings[0].primary_message)
        self.assertIn("Header", result.findings[1].primary_message)

    def test_errored_build_produces_high_severity(self):
        artifact = {
            "builds": [{
                "errCount": 3,
                "webUrl": "https://chromatic.com/build/123",
                "changes": [],
            }],
            "summary": {"total": 10, "accepted": 7, "errored": 3, "changed": 0, "unchanged": 7, "rejected": 0},
        }
        result = ChromaticNormalizer().run(artifact=artifact, repo_root=self.root)
        err_findings = [f for f in result.findings if f.category == "visual-regression-error"]
        self.assertEqual(len(err_findings), 1)
        self.assertEqual(err_findings[0].severity, "high")

    def test_rejected_change_is_high_severity(self):
        artifact = {
            "builds": [{
                "errCount": 0,
                "webUrl": "https://chromatic.com/build/456",
                "changes": [
                    {"component": "Card", "story": "Default", "status": "REJECTED", "changeUrl": None},
                ],
            }],
            "summary": {"total": 5, "accepted": 4, "errored": 0, "changed": 0, "unchanged": 4, "rejected": 1},
        }
        result = ChromaticNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].severity, "high")

    def test_non_dict_returns_empty(self):
        result = ChromaticNormalizer().run(artifact="not-a-dict", repo_root=self.root)
        self.assertEqual(len(result.findings), 0)

    def test_no_builds_returns_empty(self):
        result = ChromaticNormalizer().run(artifact={"builds": []}, repo_root=self.root)
        self.assertEqual(len(result.findings), 0)

    def test_provider_is_chromatic(self):
        self.assertEqual(ChromaticNormalizer().provider, "Chromatic")

    def test_clean_build_no_findings(self):
        artifact = {
            "builds": [{
                "errCount": 0,
                "webUrl": "https://chromatic.com/build/789",
                "changes": [],
            }],
            "summary": {"total": 10, "accepted": 10, "errored": 0, "changed": 0, "unchanged": 10, "rejected": 0},
        }
        result = ChromaticNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(len(result.findings), 0)


if __name__ == "__main__":
    unittest.main()
