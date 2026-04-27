"""Declining patch generator for `too-long` category."""
from __future__ import absolute_import

from scripts.quality.rollup_v2.patches._declining import make_decline_generator

GENERATOR_VERSION = "too_long/1.0.0"
CATEGORY = "too-long"

generate = make_decline_generator(
    reason_text="length reduction requires extract-method refactoring",
    suggested_tier="llm-fallback",
)
