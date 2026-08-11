#!/usr/bin/env python3
"""Severity floor for gate 6 (deps) - the T0 tier of the lean gate.

``osv-scanner`` exits 1 for ANY finding. The lean gate used to map that exit
straight onto a red branch, with no severity floor, no dev/prod split and no
check that a fix even exists. Measured consequence (2026-08-11): 22 of 34 open
fleet PRs were BLOCKED on ``gate-deps``, and DevExtreme PR #40 - itself a
*security* bump - sat blocked for 17 days behind an UNSCORED Go stdlib
advisory that no version bump in that PR could clear.

This module re-reads the SAME scan as JSON and splits it in two:

``BLOCK``
    NOT dev-only **AND** ``max_severity`` parses to a float >= the floor
    (default 7.0 = CVSS High) **AND** the report names a fixed version.

``T2``
    Everything else - dev-only, unscored, below the floor, or unfixable.
    T2 findings are **printed in full**. Demoted is not hidden.

Design constraint this encodes: a gate the owner cannot drive to green is a
defect regardless of what it detects. Every BLOCK row names a fixed version,
so every BLOCK is closable by an action the owner controls.

Schema notes, measured against the pinned ``osv-scanner v2.3.8`` binary:

* ``results[].packages[].dependency_groups`` is **ABSENT** for production
  dependencies (and for Go), ``["dev"]`` / ``["dev","optional"]`` otherwise.
* ``results[].packages[].groups[].max_severity`` is a **string** and is the
  **empty string** for unscored advisories (every Go stdlib advisory is
  unscored). It is not ``null`` - parse it defensively either way.
* A group's ``ids`` are not guaranteed to appear in ``vulnerabilities[]``;
  when they do not, no fixed version can be established and the finding is
  demoted rather than blocking on data we do not have.

Exit codes: ``0`` no blocking findings, ``1`` at least one blocking finding,
``2`` the report could not be read or parsed (never treated as "clean").
"""

from __future__ import absolute_import

import argparse
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Sequence, TextIO, Tuple

SEVERITY_FLOOR = 7.0
"""Default CVSS floor. 7.0 is the CVSS v3 boundary between Medium and High."""

DEV_ONLY_GROUPS = frozenset(("dev", "optional"))
"""Dependency groups that may take part in a dev-only demotion.

``optional`` alone is deliberately NOT enough: an npm ``optionalDependency``
IS installed in production. Only a package that is reachable *via* the dev
tree (i.e. its group set contains ``dev``) is demoted for scope.
"""

UNKNOWN = "<unknown>"

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_INPUT_ERROR = 2


class ReportInputError(RuntimeError):
    """The osv-scanner report could not be read or parsed."""


@dataclass(frozen=True)
class Finding:
    """One osv-scanner finding-GROUP (a package x advisory-group pair)."""

    source: str
    package: str
    version: str
    ecosystem: str
    dependency_groups: Tuple[str, ...]
    ids: Tuple[str, ...]
    max_severity_raw: str
    severity: Optional[float]
    fixed_versions: Tuple[str, ...]
    dev_only: bool
    reasons: Tuple[str, ...] = ()


def parse_severity(raw_value: Any) -> Optional[float]:
    """Parse ``max_severity`` into a float, or ``None`` when it is unscored.

    ``max_severity`` is a STRING and is ``""`` for unscored advisories. An
    unscored advisory must never collapse to ``0.0`` (that would silently read
    as "below the floor" for the wrong reason) nor to a crash.
    """
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return None


def normalize_groups(raw_value: Any) -> Tuple[str, ...]:
    """Normalise ``dependency_groups`` to a lowercased tuple.

    The key is ABSENT (``None``) for production dependencies, so anything that
    is not a list is treated as "no groups" == production.
    """
    if not isinstance(raw_value, list):
        return ()
    return tuple(str(item).strip().lower() for item in raw_value)


def is_dev_only(groups: Sequence[str]) -> bool:
    """Return True when every group is a dev-tree group AND ``dev`` is one."""
    if not groups:
        return False
    if not set(groups) <= DEV_ONLY_GROUPS:
        return False
    return "dev" in groups


def fixed_versions_for(package_entry: Mapping[str, Any], advisory_ids: Iterable[str]) -> Tuple[str, ...]:
    """Collect every ``fixed`` version published for ``advisory_ids``.

    An empty result means "this report names no fix", which is a demotion
    reason: the owner cannot bump their way out of it.
    """
    wanted = set(advisory_ids)
    found = set()
    for vulnerability in package_entry.get("vulnerabilities") or []:
        if not isinstance(vulnerability, dict) or vulnerability.get("id") not in wanted:
            continue
        for affected in vulnerability.get("affected") or []:
            if not isinstance(affected, dict):
                continue
            for affected_range in affected.get("ranges") or []:
                if not isinstance(affected_range, dict):
                    continue
                for event in affected_range.get("events") or []:
                    if isinstance(event, dict) and "fixed" in event:
                        found.add(str(event["fixed"]))
    return tuple(sorted(found))


def _finding_from_group(
    *,
    source_path: str,
    meta: Mapping[str, Any],
    groups: Tuple[str, ...],
    dev_only: bool,
    package_entry: Mapping[str, Any],
    group: Mapping[str, Any],
) -> Finding:
    """Build one ``Finding`` from a single ``groups[]`` entry."""
    ids = tuple(str(item) for item in group.get("ids") or [])
    raw_severity = group.get("max_severity", "")
    return Finding(
        source=source_path,
        package=str(meta.get("name", UNKNOWN)),
        version=str(meta.get("version", UNKNOWN)),
        ecosystem=str(meta.get("ecosystem", UNKNOWN)),
        dependency_groups=groups,
        ids=ids,
        max_severity_raw=str(raw_severity),
        severity=parse_severity(raw_severity),
        fixed_versions=fixed_versions_for(package_entry, ids),
        dev_only=dev_only,
    )


def iter_findings(document: Any) -> List[Finding]:
    """Flatten an osv-scanner JSON report into one ``Finding`` per group."""
    findings: List[Finding] = []
    if not isinstance(document, dict):
        return findings
    for result in document.get("results") or []:
        if not isinstance(result, dict):
            continue
        source_block = result.get("source")
        source_path = str(source_block.get("path", UNKNOWN)) if isinstance(source_block, dict) else UNKNOWN
        for package_entry in result.get("packages") or []:
            if not isinstance(package_entry, dict):
                continue
            raw_meta = package_entry.get("package")
            meta = raw_meta if isinstance(raw_meta, dict) else {}
            groups = normalize_groups(package_entry.get("dependency_groups"))
            dev_only = is_dev_only(groups)
            for group in package_entry.get("groups") or []:
                if not isinstance(group, dict):
                    continue
                findings.append(
                    _finding_from_group(
                        source_path=source_path,
                        meta=meta,
                        groups=groups,
                        dev_only=dev_only,
                        package_entry=package_entry,
                        group=group,
                    )
                )
    return findings


def demotion_reasons(finding: Finding, floor: float) -> Tuple[str, ...]:
    """Return every reason this finding is T2 rather than blocking (may be empty)."""
    reasons: List[str] = []
    if finding.dev_only:
        reasons.append("dev-only")
    if finding.severity is None:
        reasons.append("unscored")
    elif finding.severity < floor:
        reasons.append("below-floor")
    if not finding.fixed_versions:
        reasons.append("no-fix-available")
    return tuple(reasons)


def classify(findings: Iterable[Finding], floor: float) -> Tuple[List[Finding], List[Finding]]:
    """Split findings into ``(blocking, demoted)``, annotating demotion reasons."""
    blocking: List[Finding] = []
    demoted: List[Finding] = []
    for finding in findings:
        reasons = demotion_reasons(finding, floor)
        annotated = replace(finding, reasons=reasons)
        if reasons:
            demoted.append(annotated)
        else:
            blocking.append(annotated)
    return blocking, demoted


def format_finding(finding: Finding) -> str:
    """Render one finding as a single reviewable line."""
    advisories = ", ".join(finding.ids) or "<no advisory id>"
    severity = "-" if finding.severity is None else format(finding.severity, "g")
    scope = "+".join(finding.dependency_groups) or "prod"
    fix = "fixed in " + ", ".join(finding.fixed_versions) if finding.fixed_versions else "no published fix"
    return (
        f"{finding.package} {finding.version} ({finding.ecosystem}) [{scope}] "
        f"severity {severity} | {advisories} | {fix} | {finding.source}"
    )


def _rows(findings: Sequence[Finding], *, with_reasons: bool) -> List[str]:
    """Render a section body, or an explicit ``none`` when the section is empty."""
    if not findings:
        return ["  none"]
    rows: List[str] = []
    for finding in findings:
        prefix = ""
        if with_reasons:
            prefix = "[" + "+".join(finding.reasons) + "] "
        rows.append("  - " + prefix + format_finding(finding))
    return rows


def render_report(blocking: Sequence[Finding], demoted: Sequence[Finding], floor: float) -> str:
    """Render both tiers. A T2 finding is demoted, never hidden."""
    lines = [
        "== gate-deps severity floor (T0) ==",
        f"   BLOCK when: NOT dev-only AND CVSS >= {format(floor, 'g')} AND a fixed version exists.",
        "   Everything else is T2 inventory: reported below, never blocking.",
        "",
        f"BLOCKING ({len(blocking)}):",
    ]
    lines.extend(_rows(blocking, with_reasons=False))
    lines.append("")
    lines.append(f"T2 INVENTORY ({len(demoted)}):")
    lines.extend(_rows(demoted, with_reasons=True))
    return "\n".join(lines)


def load_document(source: str, stdin: Optional[TextIO] = None) -> Any:
    """Read the osv-scanner report from ``-`` (stdin) or a filesystem path."""
    if source == "-":
        stream = sys.stdin if stdin is None else stdin
        raw = stream.read()
    else:
        try:
            raw = Path(source).read_text(encoding="utf-8")
        except OSError as exc:
            raise ReportInputError(f"cannot read osv-scanner report '{source}': {exc}") from exc
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ReportInputError(f"osv-scanner report '{source}' is not valid JSON: {exc}") from exc


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        description="Split an osv-scanner JSON report into blocking (T0) and inventory (T2) findings.",
    )
    parser.add_argument(
        "report",
        nargs="?",
        default="-",
        help="Path to an 'osv-scanner ... --format json' report, or '-' for stdin (default).",
    )
    parser.add_argument(
        "--min-severity",
        type=float,
        default=SEVERITY_FLOOR,
        help=f"CVSS floor at or above which a fixable production finding blocks (default {SEVERITY_FLOOR}).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Classify a report, print both tiers, and return the gate exit code."""
    args = _build_parser().parse_args(argv)
    try:
        document = load_document(args.report)
    except ReportInputError as exc:
        print(f"ERROR gate-deps severity floor: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    blocking, demoted = classify(iter_findings(document), args.min_severity)
    print(render_report(blocking, demoted, args.min_severity))
    if blocking:
        print(
            f"FAIL gate-deps: {len(blocking)} blocking finding(s) - "
            "production dependency, CVSS at or above the floor, and a fix is published. Bump them.",
            file=sys.stderr,
        )
        return EXIT_BLOCKED
    print(f"PASS gate-deps: 0 blocking finding(s); {len(demoted)} demoted to T2 inventory (listed above).")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
