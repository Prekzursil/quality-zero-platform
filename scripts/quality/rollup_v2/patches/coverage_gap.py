"""Declining patch generator for `coverage-gap` category (Task 9.31)."""

from __future__ import absolute_import

from scripts.quality.rollup_v2.patches._decline_helpers import make_decline_generator

GENERATOR_VERSION = "coverage_gap/1.0.0"
CATEGORY = "coverage-gap"
generate = make_decline_generator(
    reason_text="coverage gaps require human-written test cases",
    suggested_tier="human-only",
)
