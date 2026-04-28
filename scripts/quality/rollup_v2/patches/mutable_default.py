"""Deterministic patch generator for `mutable-default` category."""
from __future__ import absolute_import

import difflib
import re
from pathlib import Path
from typing import List

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "mutable_default/1.0.0"
CATEGORY = "mutable-default"

# Matches `def f(x=[])` or `def f(x={})` patterns in function signatures
_MUTABLE_DEFAULT = re.compile(
    r"^(\s*def\s+\w+\s*\([^)]*?)(\w+)\s*=\s*(\[\]|\{\})\s*([,)])"
)


def _build_def_line_replacement(target_line: str, match: re.Match) -> str:
    """Produce ``def f(x=None, ...)`` from the matched ``def f(x=[], ...)`` line."""
    prefix = match.group(1)
    param_name = match.group(2)
    trailing = match.group(4)
    new_def_line = f"{prefix}{param_name}=None{trailing}{target_line[match.end():]}"
    if not new_def_line.endswith("\n"):  # pragma: no cover -- source lines always end with newline from splitlines(keepends=True)
        new_def_line += "\n"
    return new_def_line


def _find_body_start(lines: List[str], target_index: int) -> int:
    """Skip blank lines after the ``def`` to find where the body begins."""
    body_start = target_index + 1
    while body_start < len(lines) and lines[body_start].strip() == "":  # pragma: no cover -- blank lines between def and body are rare
        body_start += 1
    return body_start


def generate(
    finding: Finding,
    source_file_content: str,
    _repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Replace mutable default `def f(x=[])` with `def f(x=None)` + guard."""
    lines = source_file_content.splitlines(keepends=True)
    target_index = finding.line - 1
    if target_index < 0 or target_index >= len(lines):
        return PatchDeclined(
            reason_code="provider-data-insufficient",
            reason_text=f"line {finding.line} out of range",
            suggested_tier="skip",
        )

    target_line = lines[target_index]
    match = _MUTABLE_DEFAULT.match(target_line)
    if not match:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text=f"line {finding.line} does not match mutable default pattern",
            suggested_tier="llm-fallback",
        )

    new_def_line = _build_def_line_replacement(target_line, match)
    def_indent = target_line[: len(target_line) - len(target_line.lstrip())]
    body_indent = def_indent + "    "
    param_name = match.group(2)
    mutable_literal = match.group(3)
    body_start = _find_body_start(lines, target_index)
    guard_line = f"{body_indent}{param_name} = {mutable_literal} if {param_name} is None else {param_name}\n"

    patched_lines = lines.copy()
    patched_lines[target_index] = new_def_line
    patched_lines.insert(body_start, guard_line)

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
