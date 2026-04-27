"""Declining patch generator for `duplicate-code` category."""

from __future__ import absolute_import

from scripts.quality.rollup_v2.patches._decline_helpers import make_decline_generator

GENERATOR_VERSION = "duplicate_code/1.0.0"
CATEGORY = "duplicate-code"
generate = make_decline_generator(
    reason_text="duplicate code extraction is a multi-file-capable refactor",
)
