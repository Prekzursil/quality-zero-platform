#!/usr/bin/env python3
"""Assert the JS/TS coverage number the gate-3 lane just produced.

The jsts lane used to run the caller's coverage script and pass on exit 0. It
passed NO threshold and checked NO number: enforcement was delegated entirely to
the caller's vitest/karma config, and the gate never verified that one existed.
Measured 2026-08-11 on momentstudio's frontend, the run printed
``Statements : 49.57%`` and the lane printed ``PASS gate-tests-coverage jsts``.

This module reads the summary the run just produced and compares it to a number.
Evidence is taken in strength order, machine-readable artifacts first because a
printed table is a rendering of the data, not the data:

1. ``coverage/**/coverage-summary.json``  (istanbul ``json-summary``)
2. ``coverage/**/clover.xml``             (vitest's default reporter set)
3. ``coverage/**/lcov.info``              (karma / Angular default)
4. the coverage run's own printed summary (istanbul text-summary block, or the
   ``All files`` row of the text table)

The property that holds regardless of any threshold: **a run that produces no
parseable summary FAILS.** ``--min-percent`` can move the bar but can never
switch off the requirement that a real number was measured, and it is rejected
outside ``(0, 100]`` so a "0% bar" silent pass is not expressible.

Branch coverage is compared too (the charter is 100% line AND branch), except
where the report says the project has zero conditionals — that is "not
applicable", not 0%.

XML is parsed with a regex rather than ``xml.etree``, mirroring
``scripts/quality/coverage_parsers.py``: it avoids the XML-parser attack surface
the ``S`` lint rules flag, and clover's metrics live in element attributes.

Exit codes: ``0`` at or above the bar, ``1`` below it or unmeasured, ``2`` the
threshold argument itself is invalid.
"""

from __future__ import absolute_import

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

DEFAULT_MIN_PERCENT = 100.0
COVERAGE_DIR = "coverage"
EXIT_OK = 0
EXIT_BELOW_BAR = 1
EXIT_CONFIG_ERROR = 2

_CLOVER_METRIC = re.compile(r'(\w+)\s*=\s*"(\d+)"')
_TEXT_ROW = re.compile(r"^\s*(Statements|Branches|Lines)\s*:\s*([0-9.]+)%\s*\(\s*(\d+)\s*/\s*(\d+)\s*\)", re.MULTILINE)
_ALL_FILES_ROW = re.compile(r"^[|\s]*All files\s*\|(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class CoverageSummary:
    """A measured coverage result and where the number came from."""

    origin: str
    lines_pct: float
    branches_pct: Optional[float]


def _percent(covered: int, total: int) -> Optional[float]:
    """Return a percentage, or ``None`` when the denominator is zero."""
    if total <= 0:
        return None
    return round(covered * 100.0 / total, 2)


def _as_float(value: Any) -> Optional[float]:
    """Coerce a reported percentage to a float, or ``None`` when it is not one."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summary_from_json_summary(path: Path) -> Optional[CoverageSummary]:
    """Parse an istanbul ``json-summary`` report."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    total = document.get("total")
    if not isinstance(total, dict):
        return None
    lines = total.get("lines") if isinstance(total.get("lines"), dict) else {}
    branches = total.get("branches") if isinstance(total.get("branches"), dict) else {}
    lines_pct = _as_float(lines.get("pct"))
    if lines_pct is None or _as_float(lines.get("total")) in (None, 0.0):
        return None
    branches_total = _as_float(branches.get("total"))
    branches_pct = None if branches_total in (None, 0.0) else _as_float(branches.get("pct"))
    return CoverageSummary(origin=str(path), lines_pct=lines_pct, branches_pct=branches_pct)


def summary_from_clover(path: Path) -> Optional[CoverageSummary]:
    """Parse the project-level ``<metrics .../>` of a clover report."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"<metrics\b([^>]*)>", text)
    if match is None:
        return None
    metrics = {key.lower(): int(value) for key, value in _CLOVER_METRIC.findall(match.group(1))}
    lines_pct = _percent(metrics.get("coveredstatements", 0), metrics.get("statements", 0))
    if lines_pct is None:
        return None
    branches_pct = _percent(metrics.get("coveredconditionals", 0), metrics.get("conditionals", 0))
    return CoverageSummary(origin=str(path), lines_pct=lines_pct, branches_pct=branches_pct)


def summary_from_lcov(path: Path) -> Optional[CoverageSummary]:
    """Sum the ``LF``/``LH`` and ``BRF``/``BRH`` counters of an lcov report."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    totals = {"LF": 0, "LH": 0, "BRF": 0, "BRH": 0}
    for line in text.splitlines():
        key, _, raw = line.partition(":")
        if key not in totals:
            continue
        try:
            totals[key] += int(raw.strip())
        except ValueError:
            continue
    lines_pct = _percent(totals["LH"], totals["LF"])
    if lines_pct is None:
        return None
    return CoverageSummary(origin=str(path), lines_pct=lines_pct, branches_pct=_percent(totals["BRH"], totals["BRF"]))


def _summary_from_text_block(text: str) -> Optional[CoverageSummary]:
    """Parse the istanbul ``Coverage summary`` block."""
    rows = {}
    for label, pct, _covered, total in _TEXT_ROW.findall(text):
        if int(total) > 0:
            rows[label] = float(pct)
    lines_pct = rows.get("Lines", rows.get("Statements"))
    if lines_pct is None:
        return None
    return CoverageSummary(
        origin="the coverage run's printed summary",
        lines_pct=lines_pct,
        branches_pct=rows.get("Branches"),
    )


def _summary_from_text_table(text: str) -> Optional[CoverageSummary]:
    """Parse the ``All files`` row of the istanbul / vitest text table."""
    match = _ALL_FILES_ROW.search(text)
    if match is None:
        return None
    cells = [cell.strip() for cell in match.group(1).split("|")]
    if len(cells) < 4:
        return None
    statements, branches, _functions, lines = (_as_float(cell) for cell in cells[:4])
    if lines is None or statements is None:
        return None
    return CoverageSummary(
        origin="the coverage run's printed summary",
        lines_pct=lines,
        branches_pct=branches,
    )


def summary_from_text(text: str) -> Optional[CoverageSummary]:
    """Parse whichever printed coverage shape the runner emitted."""
    return _summary_from_text_block(text) or _summary_from_text_table(text)


def discover(project_dir: Path, log_text: str) -> Optional[CoverageSummary]:
    """Find the strongest available coverage evidence for ``project_dir``."""
    readers = (
        ("coverage-summary.json", summary_from_json_summary),
        ("clover.xml", summary_from_clover),
        ("lcov.info", summary_from_lcov),
    )
    coverage_root = project_dir / COVERAGE_DIR
    for filename, reader in readers:
        for candidate in sorted(coverage_root.rglob(filename)):
            summary = reader(candidate)
            if summary is not None:
                return summary
    return summary_from_text(log_text)


def evaluate(summary: CoverageSummary, min_percent: float) -> List[str]:
    """Return one finding per coverage dimension that is below the bar."""
    findings: List[str] = []
    if summary.lines_pct < min_percent:
        findings.append(f"line coverage {summary.lines_pct:.2f}% is below the required {min_percent:.2f}%")
    if summary.branches_pct is not None and summary.branches_pct < min_percent:
        findings.append(f"branch coverage {summary.branches_pct:.2f}% is below the required {min_percent:.2f}%")
    return findings


def _read_log(log_path: Optional[str]) -> str:
    """Read the captured coverage-run output, tolerating a missing file."""
    if not log_path:
        return ""
    try:
        return Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description="Assert the coverage a JS/TS project just measured.")
    parser.add_argument("--project-dir", required=True, help="Directory the coverage script ran in.")
    parser.add_argument("--log", default=None, help="File holding the coverage run's captured output.")
    parser.add_argument(
        "--min-percent",
        type=float,
        default=DEFAULT_MIN_PERCENT,
        help=f"Required line and branch coverage, in (0, 100] (default {DEFAULT_MIN_PERCENT}).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Assert the measured coverage and return the lane's exit code."""
    args = _build_parser().parse_args(argv)
    if not 0 < args.min_percent <= 100:
        print(
            f"ERROR gate-tests-coverage jsts: --min-percent {args.min_percent} must be greater than 0 "
            "and at most 100. A 0% bar is a silent pass wearing a number; a bar above 100 is unreachable.",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR
    project_dir = Path(args.project_dir)
    summary = discover(project_dir, _read_log(args.log))
    if summary is None:
        print(
            f"FAIL gate-tests-coverage jsts: the coverage script ran in '{project_dir}' but produced NO parseable "
            "coverage summary, so the gate cannot pass it unmeasured. Looked for "
            "coverage/**/coverage-summary.json, coverage/**/clover.xml, coverage/**/lcov.info and a printed "
            "summary. Remediation: add the 'json-summary' (or 'lcov') coverage reporter to the project's "
            "vitest/jest/karma config.",
            file=sys.stderr,
        )
        return EXIT_BELOW_BAR
    branches = "n/a (no conditionals)" if summary.branches_pct is None else f"{summary.branches_pct:.2f}%"
    print(
        f"gate-tests-coverage jsts: measured line {summary.lines_pct:.2f}%, branch {branches} "
        f"against a required {args.min_percent:.2f}% (source: {summary.origin})"
    )
    findings = evaluate(summary, args.min_percent)
    if findings:
        for finding in findings:
            print(f"FAIL gate-tests-coverage jsts: {finding}.", file=sys.stderr)
        return EXIT_BELOW_BAR
    print("PASS gate-tests-coverage jsts: the measured coverage meets the required threshold.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
