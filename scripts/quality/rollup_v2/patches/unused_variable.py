"""Deterministic patch generator for `unused-variable` category."""

from __future__ import absolute_import

import re

from scripts.quality.rollup_v2.patches._drop_line_helpers import make_drop_line_generator

GENERATOR_VERSION = "unused_variable/1.0.0"
CATEGORY = "unused-variable"

# Matches simple assignment like `x = <expr>` (not augmented assignment)
_SIMPLE_ASSIGN = re.compile(r"^\s*[A-Za-z_]\w*\s*=\s*.+")

generate = make_drop_line_generator(
    line_pattern=_SIMPLE_ASSIGN,
    decline_message="is not a simple assignment",
    category=CATEGORY,
    generator_version=GENERATOR_VERSION,
    confidence="medium",
)
