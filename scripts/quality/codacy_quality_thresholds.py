#!/usr/bin/env python3
"""Codacy multi-dimensional quality threshold checks.

Codacy's ``issues/search`` endpoint only reports the open-issue count, but the
gate must also hard-block when *complexity* or *duplication* drift past the
project's goals, or when *coverage* is missing or below the strict-zero floor.
This module isolates the additional repository-analysis fetch + threshold
evaluation so ``check_codacy_zero.py`` stays focused on issue counting.

Why a separate floor for coverage:
  Codacy's default ``minCoveragePercentage`` is 60. The fleet contract is
  100% line+branch — non-uploaded coverage is treated as a hard fail and
  any coverage below 100% counts as drift, regardless of the project's
  Codacy-side goal.
"""

from __future__ import absolute_import

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping

JsonRequester = Callable[[str, str], Dict[str, Any]]
"""Signature for the HTTP helper that returns parsed JSON for a Codacy URL."""

DEFAULT_COVERAGE_FLOOR = 100
"""Strict-zero coverage floor (percent). Overrides Codacy's default 60."""


@dataclass(frozen=True)
class CodacyQualitySnapshot:  # pylint: disable=too-many-instance-attributes
    """Resolved metrics + thresholds for one Codacy repository.

    Pairs each Codacy quality metric with its goal (cap or floor) so the
    threshold evaluator can compute findings in one pass without a second
    fetch. Pylint's R0902 (max 7 instance attributes) does not fit a
    value-object that mirrors the API payload's metric+goal shape.
    """

    issues_percentage: float | None
    complex_files_percentage: float | None
    duplication_percentage: float | None
    coverage_percentage: float | None
    coverage_uploaded: bool
    max_issue_percentage: float | None
    max_complex_files_percentage: float | None
    max_duplicated_files_percentage: float | None
    min_coverage_percentage: float | None


def _coerce_number(value: Any) -> float | None:
    """Return ``value`` as ``float`` when it is numeric, else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _coverage_data(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the ``coverage`` sub-mapping from one Codacy analysis payload."""
    coverage = payload.get("coverage")
    return coverage if isinstance(coverage, dict) else {}


_COVERAGE_PERCENTAGE_KEYS = (
    "coveragePercentage", "linesCoveragePercentage", "filesCoveragePercentage",
)


def _coverage_uploaded(coverage: Mapping[str, Any]) -> bool:
    """Return whether coverage data was actually uploaded (not just file count).

    Codacy returns ``{"numberTotalFiles": N}`` even when no coverage report
    has been pushed — the file count alone is not a coverage signal.
    """
    return any(
        _coerce_number(coverage.get(key)) is not None
        for key in _COVERAGE_PERCENTAGE_KEYS
    )


def _coverage_percentage(coverage: Mapping[str, Any]) -> float | None:
    """Pick the most relevant coverage percentage from a Codacy payload."""
    for key in _COVERAGE_PERCENTAGE_KEYS:
        value = _coerce_number(coverage.get(key))
        if value is not None:
            return value
    return None


def parse_quality_snapshot(payload: Mapping[str, Any]) -> CodacyQualitySnapshot:
    """Convert one Codacy repository-analysis payload to a typed snapshot."""
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        data = {}
    goals = data.get("goals") if isinstance(data.get("goals"), dict) else {}
    coverage = _coverage_data(data)
    return CodacyQualitySnapshot(
        issues_percentage=_coerce_number(data.get("issuesPercentage")),
        complex_files_percentage=_coerce_number(data.get("complexFilesPercentage")),
        duplication_percentage=_coerce_number(data.get("duplicationPercentage")),
        coverage_percentage=_coverage_percentage(coverage),
        coverage_uploaded=_coverage_uploaded(coverage),
        max_issue_percentage=_coerce_number(goals.get("maxIssuePercentage")),
        max_complex_files_percentage=_coerce_number(goals.get("maxComplexFilesPercentage")),
        max_duplicated_files_percentage=_coerce_number(goals.get("maxDuplicatedFilesPercentage")),
        min_coverage_percentage=_coerce_number(goals.get("minCoveragePercentage")),
    )


def fetch_repository_quality(
    repository_analysis_url: str, token: str, *, request_json: JsonRequester,
) -> CodacyQualitySnapshot:
    """Fetch one Codacy repository-analysis payload and parse it."""
    payload = request_json(repository_analysis_url, token)
    return parse_quality_snapshot(payload)


def _complexity_finding(snapshot: CodacyQualitySnapshot) -> str | None:
    """Return a finding string when complexity has drifted past the goal."""
    actual = snapshot.complex_files_percentage
    cap = snapshot.max_complex_files_percentage
    if actual is None or cap is None or actual <= cap:
        return None
    return (
        f"Codacy complex-files percentage is {actual:g}% "
        f"(goal: <= {cap:g}%)."
    )


def _duplication_finding(snapshot: CodacyQualitySnapshot) -> str | None:
    """Return a finding string when duplication has drifted past the goal."""
    actual = snapshot.duplication_percentage
    cap = snapshot.max_duplicated_files_percentage
    if actual is None or cap is None or actual <= cap:
        return None
    return (
        f"Codacy duplication percentage is {actual:g}% "
        f"(goal: <= {cap:g}%)."
    )


def _coverage_findings(
    snapshot: CodacyQualitySnapshot, *, coverage_floor: float,
) -> List[str]:
    """Return findings for missing or below-floor coverage."""
    if not snapshot.coverage_uploaded:
        return [
            "Codacy coverage is missing — no coverage report was uploaded "
            f"(strict-zero floor: {coverage_floor:g}% line+branch).",
        ]
    actual = snapshot.coverage_percentage
    if actual is None:
        return [
            "Codacy coverage payload omitted a percentage value — "
            "treat as missing per strict-zero contract.",
        ]
    if actual < coverage_floor:
        return [
            f"Codacy coverage is {actual:g}% "
            f"(strict-zero floor: {coverage_floor:g}% line+branch).",
        ]
    return []


def evaluate_quality_thresholds(
    snapshot: CodacyQualitySnapshot, *, coverage_floor: float = DEFAULT_COVERAGE_FLOOR,
) -> List[str]:
    """Return the list of threshold-violation findings for one snapshot.

    Issue *count* is checked separately by the existing zero-issue gate;
    the issues-percentage goal is informational only and not enforced
    here, since the strict-zero contract requires absolute zero issues
    rather than a percentage cap.
    """
    findings: List[str] = []
    complexity = _complexity_finding(snapshot)
    if complexity:
        findings.append(complexity)
    duplication = _duplication_finding(snapshot)
    if duplication:
        findings.append(duplication)
    findings.extend(_coverage_findings(snapshot, coverage_floor=coverage_floor))
    return findings
