"""Deterministic patch generator for `tab-vs-space` category."""
from __future__ import absolute_import

import re
from pathlib import Path

from scripts.quality.rollup_v2.patches._per_line import apply_line_transform
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
    _repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Convert leading tabs to 4 spaces."""
    return apply_line_transform(
        finding=finding,
        source_file_content=source_file_content,
        transform_line=_replace_leading_tabs,
        confidence="high",
        category=CATEGORY,
        generator_version=GENERATOR_VERSION,
        decline_reason="no leading tabs found",
    )
