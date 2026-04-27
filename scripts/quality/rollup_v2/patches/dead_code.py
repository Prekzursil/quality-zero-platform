"""Deterministic patch generator for `dead-code` category."""
from __future__ import absolute_import

import difflib
from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "dead_code/1.0.0"
CATEGORY = "dead-code"


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Remove unreachable code blocks after return/raise/break/continue."""
    lines = source_file_content.splitlines(keepends=True)
    target_index = finding.line - 1
    if target_index < 0 or target_index >= len(lines):
        return PatchDeclined(
            reason_code="provider-data-insufficient",
            reason_text=f"line {finding.line} out of range",
            suggested_tier="skip",
        )

    # The finding points to the unreachable line. Remove it.
    target_line = lines[target_index]
    if not target_line.strip():
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="target line is blank",
            suggested_tier="skip",
        )

    # Get the indent level of the target line
    target_indent = len(target_line) - len(target_line.lstrip())

    # Remove consecutive unreachable lines at the same or deeper indent level
    end_index = target_index
    while end_index < len(lines):
        line = lines[end_index]
        stripped = line.strip()
        if stripped == "":  # pragma: no cover -- blank line within dead block; tested but branch depends on exact whitespace
            end_index += 1
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent >= target_indent:
            end_index += 1
        else:
            break

    if end_index == target_index:  # pragma: no cover -- defensive; requires target line to have lower indent than itself
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="could not identify unreachable block",
            suggested_tier="llm-fallback",
        )

    patched_lines = lines[:target_index] + lines[end_index:]

    diff = "".join(difflib.unified_diff(
        lines,
        patched_lines,
        fromfile=f"a/{finding.file}",
        tofile=f"b/{finding.file}",
    ))
    if not diff:  # pragma: no cover -- defensive; requires exact block removal to produce empty diff
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="no dead code removed",
            suggested_tier="skip",
        )
    return PatchResult(
        unified_diff=diff,
        confidence="medium",
        category=CATEGORY,
        generator_version=GENERATOR_VERSION,
        touches_files=frozenset({Path(finding.file)}),
    )
