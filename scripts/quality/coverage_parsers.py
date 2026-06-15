"""Coverage parsers."""

from __future__ import absolute_import

import re
from pathlib import Path
from typing import List, Optional, Set, Tuple

from scripts.quality.coverage_paths import (
    _coverage_source_candidates,
    _should_track_coverage_source,
)
from scripts.quality.coverage_types import CoverageStats

_XML_LINES_VALID_RE = re.compile(r'lines-valid="(\d+(?:\.\d+)?)"')
_XML_LINES_COVERED_RE = re.compile(r'lines-covered="(\d+(?:\.\d+)?)"')
_XML_BRANCHES_VALID_RE = re.compile(r'branches-valid="(\d+(?:\.\d+)?)"')
_XML_BRANCHES_COVERED_RE = re.compile(r'branches-covered="(\d+(?:\.\d+)?)"')
_XML_LINE_HITS_RE = re.compile(r'<line\b[^>]*\bhits="(\d+(?:\.\d+)?)"')
_XML_FILENAME_RE = re.compile(
    r'<[^>]+\bfilename=(?P<quote>["\'])(?P<value>.*?)(?P=quote)'
)
_XML_SOURCE_RE = re.compile(r"<source>(?P<value>.*?)</source>")


def coverage_sources_from_xml(path: Path) -> Set[str]:
    """Handle coverage sources from xml."""
    text = path.read_text(encoding="utf-8")
    covered_sources: Set[str] = set()
    source_roots = [
        match.group("value").strip() for match in _XML_SOURCE_RE.finditer(text)
    ]
    for match in _XML_FILENAME_RE.finditer(text):
        for filename in _coverage_source_candidates(match.group("value"), source_roots):
            if _should_track_coverage_source(filename):
                covered_sources.add(filename)
                break
    return covered_sources


def _lcov_base_dir(path: Path) -> str:
    """Derive the report base dir from an lcov file path.

    LCOV ``SF:`` values are frequently relative to the package that owns
    the report (e.g. ``apps/web/coverage/lcov.info`` records ``SF:src/main.ts``
    for ``apps/web/src/main.ts``). The base dir is the report's parent with a
    trailing ``coverage`` segment stripped. Repo-root reports yield ``""`` so
    the historical repo-root-relative resolution is preserved unchanged.
    """
    parent = path.parent
    base = parent.parent if parent.name == "coverage" else parent
    base_text = base.as_posix()
    return "" if base_text == "." else base_text


def _resolve_lcov_source(raw_sf: str, base_dir: str) -> Optional[str]:
    """Resolve one ``SF:`` value to the first trackable repo source path.

    Mirrors the XML parser: candidates are the raw-normalized value FIRST
    (backward compatible with repo-root-relative ``SF:`` paths) and then the
    base-dir-prefixed value. Returns ``None`` when nothing trackable matches.
    """
    for candidate in _coverage_source_candidates(raw_sf, [base_dir]):
        if _should_track_coverage_source(candidate):
            return candidate
    return None


def coverage_sources_from_lcov(path: Path) -> Set[str]:
    """Handle coverage sources from lcov."""
    base_dir = _lcov_base_dir(path)
    covered_sources: Set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("SF:"):
            continue
        resolved = _resolve_lcov_source(line.split(":", 1)[1], base_dir)
        if resolved is not None:
            covered_sources.add(resolved)
    return covered_sources


def parse_coverage_xml(name: str, path: Path) -> CoverageStats:
    """Handle parse coverage xml."""
    text = path.read_text(encoding="utf-8")
    if counts := _parse_xml_totals(text):
        total, covered, branch_total, branch_covered = counts
        return CoverageStats(
            name=name,
            path=str(path),
            covered=covered,
            total=total,
            branch_covered=branch_covered,
            branch_total=branch_total,
        )

    total = 0
    covered = 0
    for hits_raw in _XML_LINE_HITS_RE.findall(text):
        total += 1
        if int(float(hits_raw)) > 0:
            covered += 1
    return CoverageStats(name=name, path=str(path), covered=covered, total=total)


def _parse_xml_totals(text: str) -> Optional[Tuple[int, int, int, int]]:
    """Handle parse xml totals."""
    lines_valid_match = _XML_LINES_VALID_RE.search(text)
    lines_covered_match = _XML_LINES_COVERED_RE.search(text)
    if not (lines_valid_match and lines_covered_match):
        return None

    branches_valid_match = _XML_BRANCHES_VALID_RE.search(text)
    branches_covered_match = _XML_BRANCHES_COVERED_RE.search(text)
    return (
        int(float(lines_valid_match.group(1))),
        int(float(lines_covered_match.group(1))),
        int(float(branches_valid_match.group(1))) if branches_valid_match else 0,
        int(float(branches_covered_match.group(1))) if branches_covered_match else 0,
    )


def _lcov_counter_key(line: str) -> Optional[str]:
    """Handle lcov counter key."""
    if line.startswith("LF:"):
        return "total"
    if line.startswith("LH:"):
        return "covered"
    if line.startswith("BRF:"):
        return "branch_total"
    if line.startswith("BRH:"):
        return "branch_covered"
    return None


def _iter_included_lcov_lines(path: Path) -> List[str]:
    """Handle iter included lcov lines."""
    base_dir = _lcov_base_dir(path)
    included_lines: List[str] = []
    include_record = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            include_record = (
                _resolve_lcov_source(line.split(":", 1)[1], base_dir) is not None
            )
            continue
        if line == "end_of_record":
            include_record = False
            continue
        if include_record:
            included_lines.append(line)
    return included_lines


def parse_lcov(name: str, path: Path) -> CoverageStats:
    """Handle parse lcov."""
    counters = {
        "total": 0,
        "covered": 0,
        "branch_total": 0,
        "branch_covered": 0,
    }
    for line in _iter_included_lcov_lines(path):
        if counter_key := _lcov_counter_key(line):
            counters[counter_key] += int(line.split(":", 1)[1])
    return CoverageStats(
        name=name,
        path=str(path),
        covered=counters["covered"],
        total=counters["total"],
        branch_covered=counters["branch_covered"],
        branch_total=counters["branch_total"],
    )
