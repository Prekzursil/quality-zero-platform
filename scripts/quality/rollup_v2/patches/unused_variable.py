"""Deterministic patch generator for `unused-variable` category."""
from __future__ import absolute_import

import re

from scripts.quality.rollup_v2.patches._per_line import make_line_removal_generator

GENERATOR_VERSION = "unused_variable/1.0.0"
CATEGORY = "unused-variable"

# Matches simple assignment like `x = <expr>` (not augmented assignment)
_SIMPLE_ASSIGN = re.compile(r"^\s*[A-Za-z_]\w*\s*=\s*.+")

generate = make_line_removal_generator(
    guard_pattern=_SIMPLE_ASSIGN,
    guard_decline_reason_template="line {line} is not a simple assignment",
    confidence="medium",
    category=CATEGORY,
    generator_version=GENERATOR_VERSION,
)
