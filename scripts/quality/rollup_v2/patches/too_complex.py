"""Declining patch generator for `too-complex` category."""
from __future__ import absolute_import

from scripts.quality.rollup_v2.patches._declining import make_decline_generator

GENERATOR_VERSION = "too_complex/1.0.0"
CATEGORY = "too-complex"

generate = make_decline_generator(
    reason_text="complexity reduction requires structural refactoring",
    suggested_tier="llm-fallback",
)
