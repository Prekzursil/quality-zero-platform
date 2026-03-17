#!/usr/bin/env python3
from __future__ import absolute_import

import argparse
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Set, Tuple

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import (
    DEFAULT_COVERAGE_JSON,
    DEFAULT_COVERAGE_MD,
    NONE_BULLET,
    safe_output_path,
    utc_timestamp,
    write_report,
)


@dataclass
class CoverageStats:
    name: str
    path: str
    covered: int
    total: int

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 100.0
        return (self.covered / self.total) * 100.0


_PAIR_RE = re.compile(r"^(?P<name>[^=]+)=(?P<path>.+)$")
_XML_LINES_VALID_RE = re.compile(r'lines-valid="(\d+(?:\.\d+)?)"')
_XML_LINES_COVERED_RE = re.compile(r'lines-covered="(\d+(?:\.\d+)?)"')
_XML_LINE_HITS_RE = re.compile(r"<line\b[^>]*\bhits=\"(\d+(?:\.\d+)?)\"")
_XML_FILENAME_RE = re.compile(r"""<[^>]+\bfilename=(?P<quote>["'])(?P<value>.*?)(?P=quote)""")
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assert minimum coverage for all declared components.")
    parser.add_argument("--xml", action="append", default=[], help="Coverage XML input: name=path")
    parser.add_argument("--lcov", action="append", default=[], help="LCOV input: name=path")
    parser.add_argument(
        "--require-source",
        action="append",
        default=[],
        help="Workspace-relative file or directory that must appear in the coverage inputs.",
    )
    parser.add_argument(
        "--min-percent",
        type=float,
        default=100.0,
        help="Minimum required coverage percentage for each component and the combined summary.",
    )
    parser.add_argument("--out-json", default=DEFAULT_COVERAGE_JSON)
    parser.add_argument("--out-md", default=DEFAULT_COVERAGE_MD)
    return parser.parse_args()


def parse_named_path(value: str) -> Tuple[str, Path]:
    match = _PAIR_RE.match(value.strip())
    if not match:
        raise ValueError(f"Invalid input '{value}'. Expected format: name=path")
    return match.group("name").strip(), Path(match.group("path").strip())


def parse_coverage_xml(name: str, path: Path) -> CoverageStats:
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


def parse_lcov(name: str, path: Path) -> CoverageStats:
    total = 0
    covered = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("LF:"):
            total += int(line.split(":", 1)[1])
        elif line.startswith("LH:"):
            covered += int(line.split(":", 1)[1])
    return CoverageStats(name=name, path=str(path), covered=covered, total=total)


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
        if not line.startswith("SF:"):
            continue
        filename = _normalize_source_path(line.split(":", 1)[1])
        if filename:
            covered_sources.add(filename)
    return covered_sources


def _matches_required_source(source_path: str, required_source: str) -> bool:
    normalized_required = _normalize_source_path(required_source).rstrip("/")
    if not normalized_required:
        return False
    return source_path == normalized_required or source_path.startswith(f"{normalized_required}/")


def _find_missing_required_sources(reported_sources: Set[str], required_sources: List[str]) -> List[str]:
    missing: List[str] = []
    for required_source in required_sources:
        normalized_required = _normalize_source_path(required_source).rstrip("/")
        if not normalized_required:
            continue
        if any(_matches_required_source(source_path, normalized_required) for source_path in reported_sources):
            continue
        missing.append(normalized_required)
    return missing


def _is_tests_only_report(reported_sources: Set[str]) -> bool:
    return bool(reported_sources) and all(
        source_path == "tests" or source_path.startswith("tests/") for source_path in reported_sources
    )


def _coverage_threshold_findings(stats: List[CoverageStats], min_percent: float) -> List[str]:
    findings: List[str] = []
    for item in stats:
        if item.percent < min_percent:
            findings.append(
                f"{item.name} coverage below {min_percent:.2f}%: {item.percent:.2f}% ({item.covered}/{item.total})"
            )

    combined_total = sum(item.total for item in stats)
    combined_covered = sum(item.covered for item in stats)
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


def evaluate(
    stats: List[CoverageStats],
    min_percent: float,
    *,
    required_sources: List[str] | None = None,
    reported_sources: Set[str] | None = None,
) -> Tuple[str, List[str]]:
    normalized_sources = reported_sources or set()
    findings = _coverage_threshold_findings(stats, min_percent)
    findings.extend(_required_source_findings(normalized_sources, list(required_sources or [])))
    return ("pass" if not findings else "fail", findings)


def _render_md(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Coverage 100 Gate",
        "",
        f"- Status: `{payload['status']}`",
        f"- Minimum required coverage: `{payload['min_percent']:.2f}%`",
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
        "",
        "## Components",
    ]
    components = payload.get("components", [])
    if components:
        for item in components:
            lines.append(
                f"- `{item['name']}`: `{item['percent']:.2f}%` ({item['covered']}/{item['total']}) from `{item['path']}`"
            )
    else:
        lines.append(NONE_BULLET)

    lines.extend(["", "## Covered sources"])
    sources = payload.get("covered_sources", [])
    if sources:
        for source_path in sources:
            lines.append(f"- `{source_path}`")
    else:
        lines.append(NONE_BULLET)

    lines.extend(["", "## Findings"])
    lines.extend([f"- {finding}" for finding in payload.get("findings", [])] or [NONE_BULLET])
    return "\n".join(lines) + "\n"


def _collect_coverage_inputs(args: argparse.Namespace) -> Tuple[List[CoverageStats], Set[str]]:
    stats: List[CoverageStats] = []
    covered_sources: Set[str] = set()
    for item in args.xml:
        name, path = parse_named_path(item)
        stats.append(parse_coverage_xml(name, path))
        covered_sources.update(coverage_sources_from_xml(path))
    for item in args.lcov:
        name, path = parse_named_path(item)
        stats.append(parse_lcov(name, path))
        covered_sources.update(coverage_sources_from_lcov(path))
    return stats, covered_sources


def _build_payload(
    *,
    stats: List[CoverageStats],
    covered_sources: Set[str],
    min_percent: float,
    status: str,
    findings: List[str],
) -> Dict[str, Any]:
    return {
        "status": status,
        "timestamp_utc": utc_timestamp(),
        "min_percent": min_percent,
        "components": [asdict(item) | {"percent": item.percent} for item in stats],
        "covered_sources": sorted(covered_sources),
        "findings": findings,
    }


def main() -> int:
    args = _parse_args()
    stats, covered_sources = _collect_coverage_inputs(args)
    if not stats:
        raise SystemExit("No coverage files were provided; pass --xml and/or --lcov inputs.")

    min_percent = max(0.0, min(100.0, float(args.min_percent)))
    status, findings = evaluate(
        stats,
        min_percent,
        required_sources=list(args.require_source),
        reported_sources=covered_sources,
    )
    payload = _build_payload(
        stats=stats,
        covered_sources=covered_sources,
        min_percent=min_percent,
        status=status,
        findings=findings,
    )
    out_json = str(safe_output_path(args.out_json, DEFAULT_COVERAGE_JSON))
    out_md = str(safe_output_path(args.out_md, DEFAULT_COVERAGE_MD))
    return_code = write_report(
        payload,
        out_json=out_json,
        out_md=out_md,
        default_json=DEFAULT_COVERAGE_JSON,
        default_md=DEFAULT_COVERAGE_MD,
        render_md=_render_md,
    )
    if return_code != 0:
        return return_code
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
