"""Helpers for line-oriented patch generators.

Several deterministic generators share two structural patterns that qlty's
smells gate previously flagged as duplication:

* ``apply_line_transform`` — split, map per-line, decline if no change, diff
  otherwise. Used by ``quote_style`` and ``tab_vs_space``.
* ``apply_line_removal`` — locate ``finding.line``, decline if out-of-range or
  the line doesn't match a regex guard, pop it, diff. Used by
  ``unused_import`` and ``unused_variable``.
"""
from __future__ import absolute_import

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

LineTransformer = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class _PatchMeta:
    """Common metadata shared by ``apply_line_transform`` / ``apply_line_removal``."""
    confidence: str
    category: str
    generator_version: str


def apply_line_transform(
    *,
    finding: Finding,
    source_file_content: str,
    transform_line: LineTransformer,
    confidence: str,
    category: str,
    generator_version: str,
    decline_reason: str,
    decline_tier: str = "skip",
) -> PatchResult | PatchDeclined | None:
    """Apply ``transform_line`` to every line; emit a unified diff or decline."""
    lines = source_file_content.splitlines(keepends=True)
    patched_lines = [transform_line(line) for line in lines]
    if patched_lines == lines:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text=decline_reason,
            suggested_tier=decline_tier,
        )
    diff = "".join(difflib.unified_diff(
        lines,
        patched_lines,
        fromfile=f"a/{finding.file}",
        tofile=f"b/{finding.file}",
    ))
    return PatchResult(
        unified_diff=diff,
        confidence=confidence,
        category=category,
        generator_version=generator_version,
        touches_files=frozenset({Path(finding.file)}),
    )


PatchGenerator = Callable[
    [Finding, str, Path],
    PatchResult | PatchDeclined | None,
]


def make_line_removal_generator(
    *,
    guard_pattern: re.Pattern[str],
    guard_decline_reason_template: str,
    confidence: str,
    category: str,
    generator_version: str,
    guard_decline_tier: str = "llm-fallback",
) -> PatchGenerator:
    """Build a ``generate(finding, source, repo_root)`` that removes one line.

    ``guard_decline_reason_template`` may include ``{line}`` to interpolate the
    finding line number into the decline reason.
    """
    meta = _PatchMeta(
        confidence=confidence, category=category, generator_version=generator_version,
    )

    def generate(
        finding: Finding,
        source_file_content: str,
        repo_root: Path,
    ) -> PatchResult | PatchDeclined | None:
        return apply_line_removal(
            finding=finding,
            source_file_content=source_file_content,
            guard=_RemovalGuard(
                pattern=guard_pattern,
                decline_reason=guard_decline_reason_template.format(
                    line=finding.line,
                ),
                decline_tier=guard_decline_tier,
            ),
            meta=meta,
        )
    return generate


@dataclass(frozen=True, slots=True)
class _RemovalGuard:
    """Configuration for ``apply_line_removal`` regex-guarded removal."""
    pattern: re.Pattern[str]
    decline_reason: str
    decline_tier: str = "llm-fallback"
    out_of_range_reason: str | None = None


def apply_line_removal(
    *,
    finding: Finding,
    source_file_content: str,
    guard: _RemovalGuard,
    meta: _PatchMeta,
) -> PatchResult | PatchDeclined | None:
    """Remove the one line at ``finding.line`` after a regex guard, or decline."""
    lines = source_file_content.splitlines(keepends=True)
    target_index = finding.line - 1
    if target_index < 0 or target_index >= len(lines):
        return PatchDeclined(
            reason_code="provider-data-insufficient",
            reason_text=guard.out_of_range_reason
            or f"line {finding.line} out of range",
            suggested_tier="skip",
        )
    target_line = lines[target_index]
    if not guard.pattern.match(target_line):
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text=guard.decline_reason,
            suggested_tier=guard.decline_tier,
        )
    patched_lines = lines.copy()
    patched_lines.pop(target_index)
    diff = "".join(difflib.unified_diff(
        lines,
        patched_lines,
        fromfile=f"a/{finding.file}",
        tofile=f"b/{finding.file}",
    ))
    return PatchResult(
        unified_diff=diff,
        confidence=meta.confidence,
        category=meta.category,
        generator_version=meta.generator_version,
        touches_files=frozenset({Path(finding.file)}),
    )
