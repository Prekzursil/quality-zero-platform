#!/usr/bin/env python3

"""Enforce the platform coverage contract for collected reports."""

from __future__ import absolute_import

import argparse
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Set, Tuple

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality import coverage_support
from scripts.quality.common import (
    DEFAULT_COVERAGE_JSON,
    DEFAULT_COVERAGE_MD,
    NONE_BULLET,
    utc_timestamp,
    write_report,
)
from scripts.quality.coverage_support import (
    _coverage_threshold_findings,
    _required_source_findings,
    coverage_sources_from_lcov,
    coverage_sources_from_xml,
    parse_coverage_xml,
    parse_lcov,
)

_find_missing_required_sources = coverage_support._find_missing_required_sources
_is_tests_only_report = coverage_support._is_tests_only_report
_matches_required_source = coverage_support._matches_required_source
_normalize_source_path = coverage_support._normalize_source_path


def _normalized_branch_min_percent(raw_value: Any) -> float | None:
    """Handle normalized branch min percent."""
    if raw_value in {"", None}:
        return None
    return max(0.0, min(100.0, float(raw_value)))


@dataclass
class CoverageStats:
    """Store per-report line and branch coverage counts."""

    name: str
    path: str
    covered: int
    total: int
    branch_covered: int = 0
    branch_total: int = 0

    @property
    def percent(self) -> float:
        """Return the line coverage percentage for this report."""
        if self.total <= 0:
            return 100.0
        return (self.covered / self.total) * 100.0

    @property
    def branch_percent(self) -> float:
        """Return the branch coverage percentage for this report."""
        if self.branch_total <= 0:
            return 100.0
        return (self.branch_covered / self.branch_total) * 100.0


@dataclass(frozen=True)
class CoverageEvaluationRequest:
    """Describe the thresholds and source expectations for a coverage run."""

    min_percent: float
    branch_min_percent: float | None = None
    required_sources: List[str] | None = None
    reported_sources: Set[str] | None = None


_PAIR_RE = re.compile(r"^(?P<name>[^=]+)=(?P<path>.+)$")


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the coverage assertion command."""
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
    parser.add_argument(
        "--branch-min-percent",
        type=float,
        default=None,
        help="Optional minimum required branch coverage percentage.",
    )
    parser.add_argument("--out-json", default=DEFAULT_COVERAGE_JSON)
    parser.add_argument("--out-md", default=DEFAULT_COVERAGE_MD)
    return parser.parse_args()


def parse_named_path(value: str) -> Tuple[str, Path]:
    """Parse a ``name=path`` coverage input declaration."""
    match = _PAIR_RE.match(value.strip())
    if not match:
        raise ValueError(f"Invalid input '{value}'. Expected format: name=path")
    return match.group("name").strip(), Path(match.group("path").strip())


def evaluate(
    stats: List[CoverageStats],
    request: CoverageEvaluationRequest,
) -> Tuple[str, List[str]]:
    """Evaluate the collected coverage reports against the requested policy."""
    normalized_sources = request.reported_sources or set()
    findings = _coverage_threshold_findings(stats, request.min_percent)
    findings.extend(coverage_support._branch_threshold_findings(stats, request.branch_min_percent))
    findings.extend(_required_source_findings(normalized_sources, list(request.required_sources or [])))
    return ("pass" if not findings else "fail", findings)


def _component_markdown_line(item: Mapping[str, Any]) -> str:
    """Render one coverage component entry for the Markdown report."""
    base_message = f"- `{item['name']}`: line=`{item['percent']:.2f}%` ({item['covered']}/{item['total']})"
    if item.get("branch_total", 0):
        return f"{base_message}, branch=`{item['branch_percent']:.2f}%` " f"({item['branch_covered']}/{item['branch_total']}) from `{item['path']}`"
    return f"{base_message} from `{item['path']}`"


def _append_bullets(lines: List[str], items: List[str], *, formatter=lambda item: f"- {item}") -> None:
    """Append formatted bullet items, or the standard empty marker."""
    if items:
        lines.extend(formatter(item) for item in items)
        return
    lines.append(NONE_BULLET)


def _branch_requirement_line(payload: Mapping[str, Any]) -> str:
    """Render the branch-threshold line for the Markdown summary."""
    branch_min_percent = payload.get("branch_min_percent")
    if branch_min_percent is None:
        return "- Minimum required branch coverage: `disabled`"
    return f"- Minimum required branch coverage: `{branch_min_percent:.2f}%`"


def _append_coverage_section(lines: List[str], title: str, items: List[Any], *, formatter) -> None:
    """Append a titled bullet section to the Markdown report."""
    lines.extend(["", title])
    _append_bullets(lines, items, formatter=formatter)


def _render_md(payload: Mapping[str, Any]) -> str:
    """Render the Markdown report for the coverage gate payload."""
    lines = [
        "# Coverage 100 Gate",
        "",
        f"- Status: `{payload['status']}`",
        f"- Minimum required coverage: `{payload['min_percent']:.2f}%`",
        _branch_requirement_line(payload),
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
    ]
    _append_coverage_section(
        lines,
        "## Components",
        list(payload.get("components", [])),
        formatter=_component_markdown_line,
    )
    _append_coverage_section(
        lines,
        "## Covered sources",
        list(payload.get("covered_sources", [])),
        formatter=lambda source_path: f"- `{source_path}`",
    )
    _append_coverage_section(
        lines,
        "## Findings",
        list(payload.get("findings", [])),
        formatter=lambda item: f"- {item}",
    )
    return "\n".join(lines) + "\n"


def _collect_coverage_inputs(args: argparse.Namespace) -> Tuple[List[CoverageStats], Set[str]]:
    """Load all declared coverage reports and normalize their covered sources."""
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


def _build_payload(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Build the JSON payload emitted by the coverage gate."""
    if args:
        raise TypeError("_build_payload expects keyword arguments only")
    try:
        stats = kwargs.pop("stats")
        covered_sources = kwargs.pop("covered_sources")
        min_percent = kwargs.pop("min_percent")
        branch_min_percent = kwargs.pop("branch_min_percent", None)
        status = kwargs.pop("status")
        findings = kwargs.pop("findings")
    except KeyError as exc:  # pragma: no cover - defensive contract guard
        raise TypeError(f"Missing required payload field: {exc.args[0]}") from exc
    if kwargs:
        raise TypeError(f"Unexpected _build_payload parameters: {', '.join(sorted(kwargs))}")
    return {
        "status": status,
        "timestamp_utc": utc_timestamp(),
        "min_percent": min_percent,
        "branch_min_percent": branch_min_percent,
        "components": [asdict(item) | {"percent": item.percent, "branch_percent": item.branch_percent} for item in stats],
        "covered_sources": sorted(covered_sources),
        "findings": findings,
    }


def main() -> int:
    """Run the coverage gate and write both JSON and Markdown reports."""
    args = _parse_args()
    stats, covered_sources = _collect_coverage_inputs(args)
    if not stats:
        raise SystemExit("No coverage files were provided; pass --xml and/or --lcov inputs.")

    min_percent = max(0.0, min(100.0, float(args.min_percent)))
    branch_min_percent = _normalized_branch_min_percent(args.branch_min_percent)
    status, findings = evaluate(
        stats,
        CoverageEvaluationRequest(
            min_percent=min_percent,
            branch_min_percent=branch_min_percent,
            required_sources=list(args.require_source),
            reported_sources=covered_sources,
        ),
    )
    payload = _build_payload(
        stats=stats,
        covered_sources=covered_sources,
        min_percent=min_percent,
        branch_min_percent=branch_min_percent,
        status=status,
        findings=findings,
    )
    return_code = write_report(
        payload,
        out_json=args.out_json,
        out_md=args.out_md,
        default_json=DEFAULT_COVERAGE_JSON,
        default_md=DEFAULT_COVERAGE_MD,
        render_md=_render_md,
    )
    if return_code != 0:
        return return_code
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
