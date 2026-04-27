"""Deterministic patch generator for `tab-vs-space` category."""

from __future__ import absolute_import

import re

from scripts.quality.rollup_v2.patches._per_line_transform_helpers import (
    make_per_line_transform_generator,
)

GENERATOR_VERSION = "tab_vs_space/1.0.0"
CATEGORY = "tab-vs-space"

_LEADING_TABS = re.compile(r"^(\t+)")


def _replace_leading_tabs(line: str) -> str:
    """Replace leading tabs with 4 spaces each."""
    m = _LEADING_TABS.match(line)
    if m:
        return " " * (4 * len(m.group(1))) + line[m.end():]
    return line


generate = make_per_line_transform_generator(
    line_transform=_replace_leading_tabs,
    no_change_reason_text="no leading tabs found",
    category=CATEGORY,
    generator_version=GENERATOR_VERSION,
    confidence="high",
)
