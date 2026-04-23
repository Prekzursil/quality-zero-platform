#!/usr/bin/env python3
"""Severity-aware rollup helper for the v2 profile's ``scanners.*.severity``.

Phase 4 of ``docs/QZP-V2-DESIGN.md`` §5.2. The existing
``build_quality_rollup.py`` aggregates per-lane gate statuses into a
single overall verdict without looking at severity — every failing
lane is treated as blocking. With v2 profiles, each scanner declares
its severity (``block`` | ``warn`` | ``info``), and the rollup must
respect it:

* ``block`` — failing lane fails the aggregate gate.
* ``warn``  — failing lane surfaces as a warning annotation but does
              not fail the gate.
* ``info``  — failing lane is recorded in the rollup output but never
              affects the gate verdict.

This module is the severity mapper the rollup calls. It takes the
resolved profile + a ``{lane: status}`` map and returns a structured
verdict listing the blocker set, the warning set, and the info set.

Kept as a standalone module so the existing rollup can adopt it
incrementally — start by wrapping ``classify_lanes`` around its
existing per-lane loop, then migrate more logic over in follow-up
increments.
"""

from __future__ import absolute_import

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()


VALID_SEVERITIES: Tuple[str, ...] = ("block", "warn", "info")
DEFAULT_SEVERITY = "block"


@dataclass(frozen=True)
class RollupVerdict:
    """Outcome of rolling up a lane-status map through profile severities.

    ``verdict`` is one of ``pass`` | ``warn`` | ``fail``:

    * ``pass`` — no failures, or every failure is ``info``-severity.
    * ``warn`` — failures exist but every one is ``warn``- or ``info``-severity.
    * ``fail`` — at least one ``block``-severity lane failed.
    """

    verdict: str
    blockers: List[str]
    warnings: List[str]
    infos: List[str]


def _normalise_severity(raw: Any) -> str:
    """Map ``raw`` onto one of ``block``/``warn``/``info`` (default ``block``)."""
    if isinstance(raw, str):
        cleaned = raw.strip().lower()
        if cleaned in VALID_SEVERITIES:
            return cleaned
    return DEFAULT_SEVERITY


def severity_map(profile: Mapping[str, Any]) -> Dict[str, str]:
    """Return ``{scanner_id: severity}`` flattened from ``profile['scanners']``.

    v2 profiles declare:

    .. code-block:: yaml

        scanners:
          codecov:
            severity: block
          socket_project_report:
            severity: info

    Entries where ``severity`` is missing or unparseable default to
    ``block`` — matching the platform's zero-tolerance ethos: unknown
    means strict.
    """
    raw = profile.get("scanners") if isinstance(profile, Mapping) else None
    if not isinstance(raw, Mapping):
        return {}
    flat: Dict[str, str] = {}
    for name, entry in raw.items():
        if not isinstance(name, str):
            continue
        severity = None
        if isinstance(entry, Mapping):
            severity = entry.get("severity")
        flat[name] = _normalise_severity(severity)
    return flat


def _status_is_failure(status: str) -> bool:
    """Return whether ``status`` counts as a failure for the rollup."""
    return str(status).strip().lower() in {"fail", "failure", "error", "red"}


def classify_lanes(
    profile: Mapping[str, Any],
    lane_statuses: Mapping[str, str],
) -> RollupVerdict:
    """Bucket each failing lane into blockers / warnings / infos.

    ``lane_statuses`` is ``{lane_id: status_string}`` — typically the
    output of a reusable-scanner-matrix run where each lane writes
    ``pass`` / ``fail``. Lanes not declared in the profile default to
    ``block`` severity (same as :func:`severity_map`).
    """
    sev = severity_map(profile)
    blockers: List[str] = []
    warnings: List[str] = []
    infos: List[str] = []
    for lane, status in lane_statuses.items():
        if not _status_is_failure(status):
            continue
        bucket = sev.get(lane, DEFAULT_SEVERITY)
        if bucket == "block":
            blockers.append(lane)
        elif bucket == "warn":
            warnings.append(lane)
        else:
            infos.append(lane)
    verdict = "pass"
    if blockers:
        verdict = "fail"
    elif warnings:
        verdict = "warn"
    return RollupVerdict(
        verdict=verdict,
        blockers=sorted(blockers),
        warnings=sorted(warnings),
        infos=sorted(infos),
    )


def failing_lanes_to_gate_output(verdict: RollupVerdict) -> Dict[str, Any]:
    """Serialise a ``RollupVerdict`` for CI step-summary consumption."""
    return {
        "verdict": verdict.verdict,
        "blockers": list(verdict.blockers),
        "warnings": list(verdict.warnings),
        "infos": list(verdict.infos),
    }


def iter_severity_entries(
    profile: Mapping[str, Any],
) -> Iterable[Tuple[str, str]]:
    """Yield ``(scanner_id, severity)`` in stable sorted order.

    Used by the docs generator + dashboard so the public severity map
    is deterministic.
    """
    sev = severity_map(profile)
    for name in sorted(sev):
        yield name, sev[name]


if __name__ == "__main__":  # pragma: no cover — ad-hoc CLI
    import json

    _payload = json.loads(sys.stdin.read())
    _verdict = classify_lanes(
        _payload.get("profile") or {},
        _payload.get("lane_statuses") or {},
    )
    print(json.dumps(failing_lanes_to_gate_output(_verdict), indent=2))
