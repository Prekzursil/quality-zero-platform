"""Deterministic patch generator for `indent-mismatch` category."""
from __future__ import absolute_import

import difflib
import re
from pathlib import Path
from typing import List

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "indent_mismatch/1.0.0"
CATEGORY = "indent-mismatch"

_INDENT = re.compile(r"^(\s*)")


def _infer_expected_indent(lines: List[str], target_index: int) -> str:
    """Walk backward from ``target_index - 1`` to find the previous non-blank
    line and derive the indent level it implies (adding one 4-space level
    when the previous line ends with ``:``).
    """
    for i in range(target_index - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        match = _INDENT.match(lines[i])
        prev_indent = match.group(1) if match else ""
        if stripped.endswith(":"):
            prev_indent += "    "
        return prev_indent
    return ""


def generate(
    finding: Finding,
    source_file_content: str,
    _repo_root: Path,
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

    target_line = lines[target_index]
    target_stripped = target_line.strip()
    if not target_stripped:  # pragma: no cover -- providers never flag blank lines for indent mismatch
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="target line is blank",
            suggested_tier="skip",
        )

    new_line = _infer_expected_indent(lines, target_index) + target_stripped
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
