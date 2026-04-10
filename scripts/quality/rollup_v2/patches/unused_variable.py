"""Deterministic patch generator for `unused-variable` category."""
from __future__ import absolute_import

import difflib
import re
from pathlib import Path

from scripts.quality.rollup_v2.types.finding import Finding
from scripts.quality.rollup_v2.types.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "unused_variable/1.0.0"
CATEGORY = "unused-variable"

# Matches simple assignment like `x = <expr>` (not augmented assignment)
_SIMPLE_ASSIGN = re.compile(r"^\s*[A-Za-z_]\w*\s*=\s*.+")


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Remove the line containing the unused variable assignment."""
    lines = source_file_content.splitlines(keepends=True)
    target_index = finding.line - 1
    if target_index < 0 or target_index >= len(lines):
        return PatchDeclined(
            reason_code="provider-data-insufficient",
            reason_text=f"line {finding.line} out of range",
            suggested_tier="skip",
        )

    target_line = lines[target_index]
    if not _SIMPLE_ASSIGN.match(target_line):
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text=f"line {finding.line} is not a simple assignment",
            suggested_tier="llm-fallback",
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
        confidence="medium",
        category=CATEGORY,
        generator_version=GENERATOR_VERSION,
        touches_files=frozenset({Path(finding.file)}),
    )
