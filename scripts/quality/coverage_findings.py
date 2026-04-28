"""Coverage findings."""

from __future__ import absolute_import

from typing import TYPE_CHECKING, List, Set, Tuple

from scripts.quality.coverage_paths import _normalize_source_path

if TYPE_CHECKING:
    from scripts.quality.coverage_types import CoverageStats


def _matches_required_source(source_path: str, required_source: str) -> bool:
    """Handle matches required source."""
    normalized_required = _normalize_source_path(required_source).rstrip("/")
    return bool(normalized_required) and (
        source_path == normalized_required
        or source_path.startswith(f"{normalized_required}/")
    )


def _find_missing_required_sources(
    reported_sources: Set[str], required_sources: List[str]
) -> List[str]:
    """Handle find missing required sources."""
    return [
        normalized_required
        for required_source in required_sources
        if (normalized_required := _normalize_source_path(required_source).rstrip("/"))
        if not any(
            _matches_required_source(source_path, normalized_required)
            for source_path in reported_sources
        )
    ]


def _is_tests_only_report(reported_sources: Set[str]) -> bool:
    """Handle is tests only report."""
    return bool(reported_sources) and all(
        source_path == "tests" or source_path.startswith("tests/")
        for source_path in reported_sources
    )


def _coverage_threshold_findings(
    stats: List["CoverageStats"], min_percent: float
) -> List[str]:
    """Handle coverage threshold findings."""
    findings: List[str] = []
    stats_list = list(stats)
    for item in stats_list:
        if item.percent < min_percent:
            findings.append(
                f"{item.name} coverage below {min_percent:.2f}%: "
                f"{item.percent:.2f}% ({item.covered}/{item.total})"
            )

    combined_total = sum(item.total for item in stats_list)
    combined_covered = sum(item.covered for item in stats_list)
    combined = (
        100.0 if combined_total <= 0 else (combined_covered / combined_total) * 100.0
    )
    if combined < min_percent:
        findings.append(
            f"combined coverage below {min_percent:.2f}%: {combined:.2f}% "
            f"({combined_covered}/{combined_total})"
        )
    return findings


def _combined_branch_coverage(stats: List["CoverageStats"]) -> Tuple[int, int, float]:
    """Handle combined branch coverage."""
    combined_total = sum(item.branch_total for item in stats)
    combined_covered = sum(item.branch_covered for item in stats)
    combined = (
        100.0 if combined_total <= 0 else (combined_covered / combined_total) * 100.0
    )
    return combined_total, combined_covered, combined


def _branch_coverage_findings_for_stats(
    stats: List["CoverageStats"], branch_min_percent: float
) -> List[str]:
    """Handle branch coverage findings for stats."""
    findings: List[str] = []
    for item in stats:
        if item.branch_percent < branch_min_percent:
            findings.append(
                f"{item.name} branch coverage below "
                f"{branch_min_percent:.2f}%: {item.branch_percent:.2f}% "
                f"({item.branch_covered}/{item.branch_total})"
            )
    return findings


def _branch_threshold_findings(
    stats: List["CoverageStats"], branch_min_percent: float | None
) -> List[str]:
    """Handle branch threshold findings."""
    if branch_min_percent is None:
        return []

    findings = [
        f"{item.name} branch coverage data missing from {item.path}"
        for item in stats
        if item.branch_total <= 0
    ]
    stats_list = [item for item in stats if item.branch_total > 0]
    findings.extend(_branch_coverage_findings_for_stats(stats_list, branch_min_percent))
    combined_total, combined_covered, combined = _combined_branch_coverage(stats_list)
    if combined_total > 0 and combined < branch_min_percent:
        findings.append(
            f"combined branch coverage below {branch_min_percent:.2f}%: "
            f"{combined:.2f}% ({combined_covered}/{combined_total})"
        )
    return findings


def _required_source_findings(
    reported_sources: Set[str], required_sources: List[str]
) -> List[str]:
    """Handle required source findings."""
    findings: List[str] = []
    if _is_tests_only_report(reported_sources):
        findings.append(

                "coverage inputs only reference tests/ paths; first-party "
                "sources are missing."

        )
    findings.extend(
        f"missing required source path: {missing_source}"
        for missing_source in _find_missing_required_sources(
            reported_sources, required_sources
        )
    )
    return findings
