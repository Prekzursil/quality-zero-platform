from __future__ import absolute_import

import re
from pathlib import Path
from typing import List, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.quality.assert_coverage_100 import CoverageStats

_XML_LINES_VALID_RE = re.compile(r'lines-valid="(\d+(?:\.\d+)?)"')
_XML_LINES_COVERED_RE = re.compile(r'lines-covered="(\d+(?:\.\d+)?)"')
_XML_LINE_HITS_RE = re.compile(r'<line\b[^>]*\bhits="(\d+(?:\.\d+)?)"')
_XML_FILENAME_RE = re.compile(r'<[^>]+\bfilename=(?P<quote>["\'])(?P<value>.*?)(?P=quote)')


def _normalize_source_path(raw_path: str) -> str:
    text = str(raw_path or "").strip().replace("\\", "/")
    text = re.sub(r"/+", "/", text)
    if not text or text == ".":
        return ""

    workspace_root = Path.cwd().resolve(strict=False).as_posix().rstrip("/")
    if text == workspace_root:
        return ""
    if text.startswith(f"{workspace_root}/"):
        return text[len(workspace_root) + 1 :]
    return text.lstrip("./")


def coverage_sources_from_xml(path: Path) -> Set[str]:
    text = path.read_text(encoding="utf-8")
    covered_sources: Set[str] = set()
    for match in _XML_FILENAME_RE.finditer(text):
        filename = _normalize_source_path(match.group("value"))
        if filename:
            covered_sources.add(filename)
    return covered_sources


def coverage_sources_from_lcov(path: Path) -> Set[str]:
    covered_sources: Set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            filename = _normalize_source_path(line.split(":", 1)[1])
            if filename:
                covered_sources.add(filename)
    return covered_sources


def _matches_required_source(source_path: str, required_source: str) -> bool:
    normalized_required = _normalize_source_path(required_source).rstrip("/")
    return bool(normalized_required) and (
        source_path == normalized_required or source_path.startswith(f"{normalized_required}/")
    )


def _find_missing_required_sources(reported_sources: Set[str], required_sources: List[str]) -> List[str]:
    missing: List[str] = []
    for required_source in required_sources:
        normalized_required = _normalize_source_path(required_source).rstrip("/")
        if normalized_required and not any(
            _matches_required_source(source_path, normalized_required) for source_path in reported_sources
        ):
            missing.append(normalized_required)
    return missing


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


def parse_coverage_xml(name: str, path: Path) -> "CoverageStats":
    from scripts.quality.assert_coverage_100 import CoverageStats

    text = path.read_text(encoding="utf-8")
    lines_valid_match = _XML_LINES_VALID_RE.search(text)
    lines_covered_match = _XML_LINES_COVERED_RE.search(text)
    if lines_valid_match and lines_covered_match:
        total = int(float(lines_valid_match.group(1)))
        covered = int(float(lines_covered_match.group(1)))
        return CoverageStats(name=name, path=str(path), covered=covered, total=total)

    total = 0
    covered = 0
    for hits_raw in _XML_LINE_HITS_RE.findall(text):
        total += 1
        if int(float(hits_raw)) > 0:
            covered += 1
    return CoverageStats(name=name, path=str(path), covered=covered, total=total)


def parse_lcov(name: str, path: Path) -> "CoverageStats":
    from scripts.quality.assert_coverage_100 import CoverageStats

    total = 0
    covered = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("LF:"):
            total += int(line.split(":", 1)[1])
        elif line.startswith("LH:"):
            covered += int(line.split(":", 1)[1])
    return CoverageStats(name=name, path=str(path), covered=covered, total=total)
