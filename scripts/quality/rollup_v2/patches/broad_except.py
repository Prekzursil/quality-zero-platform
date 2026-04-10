"""Deterministic patch generator for `broad-except` category."""
from __future__ import absolute_import

import difflib
import re
from pathlib import Path

from scripts.quality.rollup_v2.types.finding import Finding
from scripts.quality.rollup_v2.types.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "broad_except/1.0.0"
CATEGORY = "broad-except"

_EXCEPT_PATTERN = re.compile(
    r"^(\s*)except(\s+Exception|\s+BaseException|)(\s+as\s+\w+)?\s*:\s*$"
)


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Rewrite `except Exception` / bare except / `except BaseException` to a narrower tuple."""
    lines = source_file_content.splitlines(keepends=True)
    target_index = finding.line - 1
    if target_index < 0 or target_index >= len(lines):
        return PatchDeclined(
            reason_code="provider-data-insufficient",
            reason_text=f"line {finding.line} out of range for file with {len(lines)} lines",
            suggested_tier="skip",
        )
    original_line = lines[target_index]
    match = _EXCEPT_PATTERN.match(original_line)
    if not match:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text=f"line {finding.line} does not match known broad-except pattern",
            suggested_tier="llm-fallback",
        )
    indent = match.group(1)
    as_clause = match.group(3) or ""
    new_line = f"{indent}except (IOError, ValueError){as_clause}:\n"
    patched_lines = lines.copy()
    patched_lines[target_index] = new_line
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
