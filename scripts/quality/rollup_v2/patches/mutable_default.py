"""Deterministic patch generator for `mutable-default` category."""
from __future__ import absolute_import

import difflib
import re
from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "mutable_default/1.0.0"
CATEGORY = "mutable-default"

# Matches `def f(x=[])` or `def f(x={})` patterns in function signatures
_MUTABLE_DEFAULT = re.compile(
    r"^(\s*def\s+\w+\s*\([^)]*?)(\w+)\s*=\s*(\[\]|\{\})\s*([,)])"
)


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
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

    prefix = match.group(1)
    param_name = match.group(2)
    mutable_literal = match.group(3)
    trailing = match.group(4)

    # Replace `x=[]` with `x=None`
    new_def_line = f"{prefix}{param_name}=None{trailing}"
    # Reconstruct the rest of the line after the match
    rest_of_line = target_line[match.end():]
    new_def_line += rest_of_line
    if not new_def_line.endswith("\n"):  # noqa: E501  # pragma: no cover -- source lines always end with newline from splitlines(keepends=True)
        new_def_line += "\n"

    # Determine the body indent (one level deeper than function def indent)
    def_indent = target_line[: len(target_line) - len(target_line.lstrip())]
    body_indent = def_indent + "    "

    # Find the first body line to insert the guard before it
    body_start = target_index + 1
    # Skip the colon line if the def line doesn't end with ':'
    while body_start < len(lines) and lines[body_start].strip() == "":  # noqa: E501  # pragma: no cover -- blank lines between def and body are rare
        body_start += 1

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
