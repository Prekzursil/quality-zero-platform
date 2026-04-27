"""Deterministic patch generator for `quote-style` category."""

from __future__ import absolute_import

import re

from scripts.quality.rollup_v2.patches._per_line_transform_helpers import (
    make_per_line_transform_generator,
)

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


generate = make_per_line_transform_generator(
    line_transform=_replace_quotes,
    no_change_reason_text="no single-quoted strings to convert",
    category=CATEGORY,
    generator_version=GENERATOR_VERSION,
    confidence="medium",
)
