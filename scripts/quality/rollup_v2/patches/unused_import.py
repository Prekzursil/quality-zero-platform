"""Deterministic patch generator for `unused-import` category."""

from __future__ import absolute_import

import re

from scripts.quality.rollup_v2.patches._drop_line_helpers import make_drop_line_generator

GENERATOR_VERSION = "unused_import/1.0.0"
CATEGORY = "unused-import"

# Matches `import X` or `from X import Y` lines
_IMPORT_LINE = re.compile(r"^\s*(import\s+\S+|from\s+\S+\s+import\s+\S+)")

generate = make_drop_line_generator(
    line_pattern=_IMPORT_LINE,
    decline_message="does not look like an import statement",
    category=CATEGORY,
    generator_version=GENERATOR_VERSION,
    confidence="high",
)
