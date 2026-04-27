"""Deterministic patch generator for `unused-import` category."""
from __future__ import absolute_import

import re

from scripts.quality.rollup_v2.patches._per_line import make_line_removal_generator

GENERATOR_VERSION = "unused_import/1.0.0"
CATEGORY = "unused-import"

# Matches `import X` or `from X import Y` lines
_IMPORT_LINE = re.compile(r"^\s*(import\s+\S+|from\s+\S+\s+import\s+\S+)")

generate = make_line_removal_generator(
    guard_pattern=_IMPORT_LINE,
    guard_decline_reason_template=(
        "line {line} does not look like an import statement"
    ),
    confidence="high",
    category=CATEGORY,
    generator_version=GENERATOR_VERSION,
)
