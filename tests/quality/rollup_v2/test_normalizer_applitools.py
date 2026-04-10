"""Tests for Applitools normalizer (per §9.4)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers.applitools import ApplitoolsNormalizer

_FIXTURE = Path(__file__).parent / "fixtures" / "normalizers" / "applitools_sample.json"


class ApplitoolsNormalizerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def test_parses_three_findings_from_fixture(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = ApplitoolsNormalizer().run(artifact=artifact, repo_root=self.root)
        # Fixture has 2 unresolved + 1 failed = 3 findings
        self.assertEqual(len(result.findings), 3)
        self.assertEqual(len(result.normalizer_errors), 0)

    def test_failed_finding_is_high_severity(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = ApplitoolsNormalizer().run(artifact=artifact, repo_root=self.root)
        # Fixture result index 1 is Dashboard - Failed
        failed = [f for f in result.findings if "Dashboard" in f.primary_message]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].severity, "high")

    def test_unresolved_finding_is_medium_severity(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = ApplitoolsNormalizer().run(artifact=artifact, repo_root=self.root)
        # Fixture results index 0 and 2 are Unresolved
        unresolved = [f for f in result.findings if "Homepage" in f.primary_message or "Settings" in f.primary_message]
        self.assertEqual(len(unresolved), 2)
        for f in unresolved:
            self.assertEqual(f.severity, "medium")

    def test_finding_category(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = ApplitoolsNormalizer().run(artifact=artifact, repo_root=self.root)
        for f in result.findings:
            self.assertEqual(f.category, "visual-regression-diff")
            self.assertEqual(f.category_group, "quality")

    def test_finding_message_includes_test_name(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = ApplitoolsNormalizer().run(artifact=artifact, repo_root=self.root)
        messages = [f.primary_message for f in result.findings]
        self.assertTrue(any("Homepage" in m for m in messages))
        self.assertTrue(any("Dashboard" in m for m in messages))

    def test_non_dict_returns_empty(self):
        result = ApplitoolsNormalizer().run(artifact="not-a-dict", repo_root=self.root)
        self.assertEqual(len(result.findings), 0)

    def test_no_results_returns_empty(self):
        artifact = {"batchId": "test", "stepsInfo": {}, "results": []}
        result = ApplitoolsNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(len(result.findings), 0)

    def test_provider_is_applitools(self):
        self.assertEqual(ApplitoolsNormalizer().provider, "Applitools")

    def test_clean_batch_no_findings(self):
        artifact = {
            "batchId": "clean",
            "stepsInfo": {"total": 10, "passed": 10, "unresolved": 0, "failed": 0, "mismatches": 0},
            "results": [
                {"testName": "Good test", "status": "Passed", "stepCount": 5, "unresolvedCount": 0, "failedCount": 0},
            ],
        }
        result = ApplitoolsNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(len(result.findings), 0)

    def test_result_url_used_as_rule_url(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = ApplitoolsNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertIn("eyes.applitools.com", result.findings[0].corroborators[0].rule_url)


    def test_results_not_list_returns_empty(self):
        result = ApplitoolsNormalizer().run(artifact={"results": "not-list"}, repo_root=self.root)
        self.assertEqual(len(result.findings), 0)

    def test_result_not_dict_skipped(self):
        result = ApplitoolsNormalizer().run(artifact={"results": ["not-dict"]}, repo_root=self.root)
        self.assertEqual(len(result.findings), 0)

    def test_passed_status_skipped(self):
        artifact = {
            "results": [
                {"testName": "OK", "status": "Passed", "stepCount": 1, "unresolvedCount": 0, "failedCount": 0},
            ],
        }
        result = ApplitoolsNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(len(result.findings), 0)


if __name__ == "__main__":
    unittest.main()
