from __future__ import absolute_import

from typing import List, Set, TYPE_CHECKING

from scripts.quality.coverage_paths import _normalize_source_path

if TYPE_CHECKING:
    from scripts.quality.assert_coverage_100 import CoverageStats


def _matches_required_source(source_path: str, required_source: str) -> bool:
    normalized_required = _normalize_source_path(required_source).rstrip("/")
    return bool(normalized_required) and (
        source_path == normalized_required or source_path.startswith(f"{normalized_required}/")
    )


def _find_missing_required_sources(reported_sources: Set[str], required_sources: List[str]) -> List[str]:
    return [
        normalized_required
        for required_source in required_sources
        if (normalized_required := _normalize_source_path(required_source).rstrip("/"))
        if not any(_matches_required_source(source_path, normalized_required) for source_path in reported_sources)
    ]


def _is_tests_only_report(reported_sources: Set[str]) -> bool:
    return bool(reported_sources) and all(
        source_path == "tests" or source_path.startswith("tests/") for source_path in reported_sources
    )


def _coverage_threshold_findings(stats: List["CoverageStats"], min_percent: float) -> List[str]:
    findings: List[str] = []
    stats_list = list(stats)
    for item in stats_list:
        if item.percent < min_percent:
            findings.append(
                f"{item.name} coverage below {min_percent:.2f}%: {item.percent:.2f}% ({item.covered}/{item.total})"
            )

    combined_total = sum(item.total for item in stats_list)
    combined_covered = sum(item.covered for item in stats_list)
    combined = 100.0 if combined_total <= 0 else (combined_covered / combined_total) * 100.0
    if combined < min_percent:
        findings.append(
            f"combined coverage below {min_percent:.2f}%: {combined:.2f}% ({combined_covered}/{combined_total})"
        )
    return findings


def _required_source_findings(reported_sources: Set[str], required_sources: List[str]) -> List[str]:
    findings: List[str] = []
    if _is_tests_only_report(reported_sources):
        findings.append("coverage inputs only reference tests/ paths; first-party sources are missing.")
    findings.extend(
        f"missing required source path: {missing_source}"
        for missing_source in _find_missing_required_sources(reported_sources, required_sources)
    )
    return findings
