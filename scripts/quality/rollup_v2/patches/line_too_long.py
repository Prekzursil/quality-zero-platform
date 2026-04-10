"""Deterministic patch generator for `line-too-long` category."""
from __future__ import absolute_import

import difflib
import textwrap
from pathlib import Path

from scripts.quality.rollup_v2.types.finding import Finding
from scripts.quality.rollup_v2.types.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "line_too_long/1.0.0"
CATEGORY = "line-too-long"
MAX_LINE_LENGTH = 100


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Wrap lines exceeding 100 columns using textwrap."""
    lines = source_file_content.splitlines(keepends=True)
    target_index = finding.line - 1
    if target_index < 0 or target_index >= len(lines):
        return PatchDeclined(
            reason_code="provider-data-insufficient",
            reason_text=f"line {finding.line} out of range",
            suggested_tier="skip",
        )

    target_line = lines[target_index]
    # Only wrap comments and string literals; complex expressions decline
    stripped = target_line.strip()
    if stripped.startswith("#"):
        # Wrap comment
        indent = target_line[: len(target_line) - len(target_line.lstrip())]
        comment_text = stripped[2:]  # Remove '# '
        wrapped = textwrap.fill(
            comment_text,
            width=MAX_LINE_LENGTH - len(indent) - 2,
            initial_indent=indent + "# ",
            subsequent_indent=indent + "# ",
        )
        new_lines = wrapped + "\n"
    elif len(target_line.rstrip("\n")) <= MAX_LINE_LENGTH:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="line is not actually too long",
            suggested_tier="skip",
        )
    else:
        # For non-comments, decline to avoid breaking syntax
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="cannot safely wrap non-comment line",
            suggested_tier="llm-fallback",
        )

    patched_lines = lines.copy()
    patched_lines[target_index] = new_lines
    if patched_lines == lines:  # pragma: no cover -- defensive; wrap always changes long comments
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="no wrapping change needed",
            suggested_tier="skip",
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
