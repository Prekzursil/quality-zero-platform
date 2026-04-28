"""Deterministic patch generator for `line-too-long` category."""
from __future__ import absolute_import

import difflib
import textwrap
from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "line_too_long/1.0.0"
CATEGORY = "line-too-long"
MAX_LINE_LENGTH = 100


def _wrap_comment_line(target_line: str) -> str:
    """Re-flow a long ``# ...`` comment within MAX_LINE_LENGTH using textwrap."""
    stripped = target_line.strip()
    indent = target_line[: len(target_line) - len(target_line.lstrip())]
    comment_text = stripped[2:]  # Remove '# '
    wrapped = textwrap.fill(
        comment_text,
        width=MAX_LINE_LENGTH - len(indent) - 2,
        initial_indent=indent + "# ",
        subsequent_indent=indent + "# ",
    )
    return wrapped + "\n"


def _build_wrapped_replacement(
    target_line: str,
) -> str | PatchDeclined:
    """Decide what (if anything) the long line should be replaced with."""
    stripped = target_line.strip()
    if stripped.startswith("#"):
        return _wrap_comment_line(target_line)
    if len(target_line.rstrip("\n")) <= MAX_LINE_LENGTH:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="line is not actually too long",
            suggested_tier="skip",
        )
    return PatchDeclined(
        reason_code="ambiguous-fix",
        reason_text="cannot safely wrap non-comment line",
        suggested_tier="llm-fallback",
    )


def generate(
    finding: Finding,
    source_file_content: str,
    _repo_root: Path,
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

    replacement = _build_wrapped_replacement(lines[target_index])
    if isinstance(replacement, PatchDeclined):
        return replacement

    patched_lines = lines.copy()
    patched_lines[target_index] = replacement
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
