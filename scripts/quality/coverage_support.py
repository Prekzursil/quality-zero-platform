"""Coverage support."""

from __future__ import absolute_import

from typing import Any

from scripts.quality import coverage_findings, coverage_parsers, coverage_paths


def _branch_threshold_findings(*args: Any, **kwargs: Any):
    """Handle branch threshold findings."""
    return coverage_findings._branch_threshold_findings(*args, **kwargs)


def _coverage_threshold_findings(*args: Any, **kwargs: Any):
    """Handle coverage threshold findings."""
    return coverage_findings._coverage_threshold_findings(*args, **kwargs)


def _find_missing_required_sources(*args: Any, **kwargs: Any):
    """Handle find missing required sources."""
    return coverage_findings._find_missing_required_sources(*args, **kwargs)


def _is_tests_only_report(*args: Any, **kwargs: Any):
    """Handle is tests only report."""
    return coverage_findings._is_tests_only_report(*args, **kwargs)


def _matches_required_source(*args: Any, **kwargs: Any):
    """Handle matches required source."""
    return coverage_findings._matches_required_source(*args, **kwargs)


def _required_source_findings(*args: Any, **kwargs: Any):
    """Handle required source findings."""
    return coverage_findings._required_source_findings(*args, **kwargs)


def coverage_sources_from_lcov(*args: Any, **kwargs: Any):
    """Handle coverage sources from lcov."""
    return coverage_parsers.coverage_sources_from_lcov(*args, **kwargs)


def coverage_sources_from_xml(*args: Any, **kwargs: Any):
    """Handle coverage sources from xml."""
    return coverage_parsers.coverage_sources_from_xml(*args, **kwargs)


def parse_coverage_xml(*args: Any, **kwargs: Any):
    """Handle parse coverage xml."""
    return coverage_parsers.parse_coverage_xml(*args, **kwargs)


def parse_lcov(*args: Any, **kwargs: Any):
    """Handle parse lcov."""
    return coverage_parsers.parse_lcov(*args, **kwargs)


def _existing_repo_file_candidate(*args: Any, **kwargs: Any):
    """Handle existing repo file candidate."""
    return coverage_paths._existing_repo_file_candidate(*args, **kwargs)


def _normalize_source_path(*args: Any, **kwargs: Any):
    """Handle normalize source path."""
    return coverage_paths._normalize_source_path(*args, **kwargs)


def _should_track_coverage_source(*args: Any, **kwargs: Any):
    """Handle should track coverage source."""
    return coverage_paths._should_track_coverage_source(*args, **kwargs)


__all__ = [
    "_branch_threshold_findings",
    "_coverage_threshold_findings",
    "_find_missing_required_sources",
    "_is_tests_only_report",
    "_matches_required_source",
    "_required_source_findings",
    "coverage_sources_from_lcov",
    "coverage_sources_from_xml",
    "parse_coverage_xml",
    "parse_lcov",
    "_existing_repo_file_candidate",
    "_normalize_source_path",
    "_should_track_coverage_source",
]
