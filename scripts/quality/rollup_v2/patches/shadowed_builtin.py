"""Declining patch generator for `shadowed-builtin` category."""
from __future__ import absolute_import

from scripts.quality.rollup_v2.patches._declining import make_decline_generator

GENERATOR_VERSION = "shadowed_builtin/1.0.0"
CATEGORY = "shadowed-builtin"

generate = make_decline_generator(
    reason_text="renaming shadowed builtins requires multi-reference analysis",
    suggested_tier="llm-fallback",
)
