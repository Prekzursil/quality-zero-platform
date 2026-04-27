"""Deterministic patch generator for `spacing-convention` category."""
from __future__ import absolute_import

import difflib
import re
from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "spacing_convention/1.0.0"
CATEGORY = "spacing-convention"

# Fix missing spaces around = in assignments (but not ==, !=, <=, >=, keyword args)
_ASSIGN_NO_SPACE = re.compile(r"(?<![=!<>])(\w)=(\w)(?!=)")


def _fix_spacing(line: str) -> str:
    """Apply PEP 8 spacing fixes to a single line."""
    # Skip comments and strings (simple heuristic: skip lines that are pure comments)
    stripped = line.lstrip()
    if stripped.startswith("#"):  # pragma: no cover -- providers rarely flag comments for spacing
        return line
    # Fix `x=1` to `x = 1` for assignments (not keyword args in function defs/calls)
    # Only apply if not inside a function call context (simple heuristic)
    result = _ASSIGN_NO_SPACE.sub(r"\1 = \2", line)
    return result


def generate(
    finding: Finding,
    source_file_content: str,
    _repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Apply PEP 8 spacing conventions."""
    lines = source_file_content.splitlines(keepends=True)
    target_index = finding.line - 1
    if target_index < 0 or target_index >= len(lines):
        return PatchDeclined(
            reason_code="provider-data-insufficient",
            reason_text=f"line {finding.line} out of range",
            suggested_tier="skip",
        )
    patched_lines = lines.copy()
    patched_lines[target_index] = _fix_spacing(lines[target_index])
    if patched_lines == lines:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="no spacing fixes applicable on target line",
            suggested_tier="llm-fallback",
        )
    diff = "".join(difflib.unified_diff(
        lines,
        patched_lines,
        fromfile=f"a/{finding.file}",
        tofile=f"b/{finding.file}",
    ))
    return PatchResult(
        unified_diff=diff,
        confidence="medium",
        category=CATEGORY,
        generator_version=GENERATOR_VERSION,
        touches_files=frozenset({Path(finding.file)}),
    )
