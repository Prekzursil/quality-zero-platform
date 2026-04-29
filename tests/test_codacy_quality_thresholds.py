"""Tests for the Codacy multi-dimensional quality threshold checks."""

from __future__ import absolute_import

import unittest
from typing import Any, Dict

from scripts.quality.codacy_quality_thresholds import (
    CodacyQualitySnapshot,
    DEFAULT_COVERAGE_FLOOR,
    evaluate_quality_thresholds,
    fetch_repository_quality,
    parse_quality_snapshot,
)


def _qzp_payload(coverage_percentage: float | None = None) -> Dict[str, Any]:
    """Return a payload shaped like the live QZP repository-analysis response."""
    coverage: Dict[str, Any] = {"numberTotalFiles": 640}
    if coverage_percentage is not None:
        coverage["coveragePercentage"] = coverage_percentage
    return {
        "data": {
            "grade": 100,
            "gradeLetter": "A",
            "issuesPercentage": 0,
            "issuesCount": 0,
            "complexFilesPercentage": 15,
            "complexFilesCount": 34,
            "duplicationPercentage": 0,
            "loc": 17379,
            "coverage": coverage,
            "goals": {
                "maxIssuePercentage": 20,
                "maxDuplicatedFilesPercentage": 10,
                "minCoveragePercentage": 60,
                "maxComplexFilesPercentage": 10,
                "fileDuplicationBlockThreshold": 1,
                "fileComplexityValueThreshold": 20,
            },
        }
    }


class ParseQualitySnapshotTests(unittest.TestCase):
    """Verify ``parse_quality_snapshot`` extracts the right fields."""

    def test_parses_typical_qzp_payload(self) -> None:
        snapshot = parse_quality_snapshot(_qzp_payload())
        self.assertEqual(snapshot.issues_percentage, 0)
        self.assertEqual(snapshot.complex_files_percentage, 15)
        self.assertEqual(snapshot.duplication_percentage, 0)
        self.assertFalse(snapshot.coverage_uploaded)
        self.assertIsNone(snapshot.coverage_percentage)
        self.assertEqual(snapshot.max_complex_files_percentage, 10)
        self.assertEqual(snapshot.max_duplicated_files_percentage, 10)
        self.assertEqual(snapshot.min_coverage_percentage, 60)

    def test_recognises_uploaded_coverage_via_coverage_percentage(self) -> None:
        snapshot = parse_quality_snapshot(_qzp_payload(coverage_percentage=87.5))
        self.assertTrue(snapshot.coverage_uploaded)
        self.assertEqual(snapshot.coverage_percentage, 87.5)

    def test_handles_data_envelope_or_flat_payload(self) -> None:
        flat = _qzp_payload()["data"]
        snapshot = parse_quality_snapshot(flat)
        self.assertEqual(snapshot.complex_files_percentage, 15)

    def test_returns_none_fields_when_payload_missing(self) -> None:
        snapshot = parse_quality_snapshot({})
        self.assertIsNone(snapshot.complex_files_percentage)
        self.assertIsNone(snapshot.max_complex_files_percentage)
        self.assertFalse(snapshot.coverage_uploaded)


class EvaluateThresholdsTests(unittest.TestCase):
    """Verify ``evaluate_quality_thresholds`` enforces the strict-zero floors."""

    def test_qzp_baseline_fails_on_complexity_and_missing_coverage(self) -> None:
        findings = evaluate_quality_thresholds(parse_quality_snapshot(_qzp_payload()))
        self.assertEqual(len(findings), 2)
        self.assertTrue(any("complex-files" in f for f in findings))
        self.assertTrue(any("missing" in f for f in findings))

    def test_passes_when_all_thresholds_satisfied_with_full_coverage(self) -> None:
        payload = _qzp_payload(coverage_percentage=100)
        payload["data"]["complexFilesPercentage"] = 5
        findings = evaluate_quality_thresholds(parse_quality_snapshot(payload))
        self.assertEqual(findings, [])

    def test_partial_coverage_below_floor_fails(self) -> None:
        payload = _qzp_payload(coverage_percentage=95)
        payload["data"]["complexFilesPercentage"] = 5
        findings = evaluate_quality_thresholds(parse_quality_snapshot(payload))
        self.assertEqual(len(findings), 1)
        self.assertIn("95", findings[0])
        self.assertIn(f"{DEFAULT_COVERAGE_FLOOR}", findings[0])

    def test_full_coverage_passes_even_when_codacy_min_is_lower(self) -> None:
        """100% coverage clears the floor regardless of Codacy's 60% goal."""
        payload = _qzp_payload(coverage_percentage=100)
        payload["data"]["complexFilesPercentage"] = 5
        findings = evaluate_quality_thresholds(parse_quality_snapshot(payload))
        self.assertEqual(findings, [])

    def test_duplication_violation_emits_finding(self) -> None:
        payload = _qzp_payload(coverage_percentage=100)
        payload["data"]["complexFilesPercentage"] = 5
        payload["data"]["duplicationPercentage"] = 19
        findings = evaluate_quality_thresholds(parse_quality_snapshot(payload))
        self.assertEqual(len(findings), 1)
        self.assertIn("duplication", findings[0].lower())

    def test_coverage_floor_is_overridable(self) -> None:
        payload = _qzp_payload(coverage_percentage=85)
        payload["data"]["complexFilesPercentage"] = 5
        findings = evaluate_quality_thresholds(
            parse_quality_snapshot(payload), coverage_floor=80,
        )
        self.assertEqual(findings, [])

    def test_missing_thresholds_skip_those_dimensions(self) -> None:
        """Don't fail noisily when Codacy hasn't analysed the repo yet."""
        snapshot = CodacyQualitySnapshot(
            issues_percentage=None,
            complex_files_percentage=None,
            duplication_percentage=None,
            coverage_percentage=None,
            coverage_uploaded=False,
            max_issue_percentage=None,
            max_complex_files_percentage=None,
            max_duplicated_files_percentage=None,
            min_coverage_percentage=None,
        )
        findings = evaluate_quality_thresholds(snapshot)
        self.assertEqual(len(findings), 1)
        self.assertIn("missing", findings[0])


class FetchRepositoryQualityTests(unittest.TestCase):
    """Verify the HTTP-fetch wrapper passes through to ``parse_quality_snapshot``."""

    def test_invokes_request_json_and_parses_result(self) -> None:
        captured: Dict[str, Any] = {}

        def fake_request(url: str, token: str) -> Dict[str, Any]:
            captured["url"] = url
            captured["token"] = token
            return _qzp_payload()

        snapshot = fetch_repository_quality(
            "https://app.codacy.com/api/v3/example", "tok", request_json=fake_request,
        )
        self.assertEqual(captured["url"], "https://app.codacy.com/api/v3/example")
        self.assertEqual(captured["token"], "tok")
        self.assertEqual(snapshot.complex_files_percentage, 15)


if __name__ == "__main__":
    unittest.main()
