"""Deterministic patch generator for `indent-mismatch` category."""
from __future__ import absolute_import

import difflib
import re
from pathlib import Path

from scripts.quality.rollup_v2.types.finding import Finding
from scripts.quality.rollup_v2.types.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "indent_mismatch/1.0.0"
CATEGORY = "indent-mismatch"

_INDENT = re.compile(r"^(\s*)")


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Re-indent the target line to align with its context (4-space convention)."""
    lines = source_file_content.splitlines(keepends=True)
    target_index = finding.line - 1
    if target_index < 0 or target_index >= len(lines):
        return PatchDeclined(
            reason_code="provider-data-insufficient",
            reason_text=f"line {finding.line} out of range",
            suggested_tier="skip",
        )
    # Look for the previous non-blank line to infer expected indent
    prev_indent = ""
    for i in range(target_index - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped:
            m = _INDENT.match(lines[i])
            prev_indent = m.group(1) if m else ""
            # If prev line ends with ':', expect one more indent level
            if stripped.endswith(":"):
                prev_indent += "    "
            break

    target_line = lines[target_index]
    target_stripped = target_line.strip()
    if not target_stripped:  # pragma: no cover -- providers never flag blank lines for indent mismatch
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="target line is blank",
            suggested_tier="skip",
        )

    new_line = prev_indent + target_stripped
    if target_line.endswith("\n"):
        new_line += "\n"

    if new_line == target_line:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="indentation already correct",
            suggested_tier="skip",
        )

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
