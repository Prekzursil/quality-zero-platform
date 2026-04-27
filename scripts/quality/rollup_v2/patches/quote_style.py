"""Deterministic patch generator for `quote-style` category."""
from __future__ import absolute_import

import re
from pathlib import Path

from scripts.quality.rollup_v2.patches._per_line import apply_line_transform
from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "quote_style/1.0.0"
CATEGORY = "quote-style"

# Matches single-quoted strings that are not inside f-strings and don't contain
# double quotes (which would require escaping if converted).
_SINGLE_QUOTED = re.compile(r"(?<!['\"])(?<!f)'([^'\\]*(?:\\.[^'\\]*)*)'")


def _replace_quotes(line: str) -> str:
    """Replace single-quoted strings with double-quoted, preserving content."""
    def _swap(m: re.Match[str]) -> str:
        content = m.group(1)
        # Don't convert if the content contains unescaped double quotes
        if '"' in content:  # pragma: no cover -- requires single-quoted string containing double quotes
            return m.group(0)
        # Unescape single quotes, escape double quotes
        content = content.replace("\\'", "'")
        return f'"{content}"'
    return _SINGLE_QUOTED.sub(_swap, line)


def generate(
    finding: Finding,
    source_file_content: str,
    _repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Convert single-quoted strings to double-quoted."""
    return apply_line_transform(
        finding=finding,
        source_file_content=source_file_content,
        transform_line=_replace_quotes,
        confidence="medium",
        category=CATEGORY,
        generator_version=GENERATOR_VERSION,
        decline_reason="no single-quoted strings to convert",
    )
