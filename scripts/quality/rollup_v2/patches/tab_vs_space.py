"""Deterministic patch generator for `tab-vs-space` category."""
from __future__ import absolute_import

import difflib
import re
from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "tab_vs_space/1.0.0"
CATEGORY = "tab-vs-space"

_LEADING_TABS = re.compile(r"^(\t+)")


def _replace_leading_tabs(line: str) -> str:
    """Replace leading tabs with 4 spaces each."""
    m = _LEADING_TABS.match(line)
    if m:
        return " " * (4 * len(m.group(1))) + line[m.end():]
    return line


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Convert leading tabs to 4 spaces."""
    lines = source_file_content.splitlines(keepends=True)
    patched_lines = [_replace_leading_tabs(line) for line in lines]
    if patched_lines == lines:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="no leading tabs found",
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
        confidence="high",
        category=CATEGORY,
        generator_version=GENERATOR_VERSION,
        touches_files=frozenset({Path(finding.file)}),
    )
