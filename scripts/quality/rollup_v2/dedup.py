"""Hybrid dedup + corroborator merge (per design §3.3 + §A.3.2)."""
from __future__ import absolute_import

from dataclasses import replace
from typing import Dict, Iterable, List

from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    CATEGORY_GROUP_SECURITY,
    Finding,
)
from scripts.quality.rollup_v2.severity import max_severity


def dedup(findings: Iterable[Finding]) -> List[Finding]:
    """Deduplicate findings per §3.3 hybrid algorithm.

    Security + quality: key = (file, line, category)
    Style: key = (file, line) only
    """
    buckets: Dict[tuple, List[Finding]] = {}
    for f in findings:
        if f.category_group in (CATEGORY_GROUP_SECURITY, CATEGORY_GROUP_QUALITY):
            key = (f.file, f.line, f.category)
        else:  # style
            key = (f.file, f.line)
        buckets.setdefault(key, []).append(f)
    merged: List[Finding] = []
    for bucket_findings in buckets.values():
        if len(bucket_findings) == 1:
            merged.append(bucket_findings[0])
        else:
            merged.append(merge_corroborators(bucket_findings))
    return merged


def merge_corroborators(findings: List[Finding]) -> Finding:
    """Merge a bucket of findings into a single canonical finding per §A.3.2."""
    primary = _pick_primary_by_provider_priority(findings)
    severity = max_severity([f.severity for f in findings])
    all_corroborators = tuple(c for f in findings for c in f.corroborators)
    return replace(
        primary,
        severity=severity,
        corroboration="multi" if len(findings) >= 2 else "single",
        corroborators=all_corroborators,
    )


def _pick_primary_by_provider_priority(findings: List[Finding]) -> Finding:
    """Return the finding whose primary corroborator has the lowest rank (highest priority)."""

    def rank(f: Finding) -> int:
        if not f.corroborators:  # pragma: no cover -- defensive; normalizers always set corroborators
            return 99
        return min(c.provider_priority_rank for c in f.corroborators)

    return min(findings, key=rank)


def assign_stable_ids(findings: List[Finding]) -> List[Finding]:
    """Re-number findings as qzp-NNNN sorted by (file, line, category).

    Returns a new list — does NOT mutate inputs (frozen dataclasses + replace).
    """
    sorted_findings = sorted(findings, key=lambda f: (f.file, f.line, f.category))
    return [
        replace(f, finding_id=f"qzp-{i:04d}")
        for i, f in enumerate(sorted_findings, start=1)
    ]
