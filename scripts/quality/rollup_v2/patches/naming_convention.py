"""Declining patch generator for `naming-convention` category."""

from __future__ import absolute_import

from scripts.quality.rollup_v2.patches._decline_helpers import make_decline_generator

GENERATOR_VERSION = "naming_convention/1.0.0"
CATEGORY = "naming-convention"
generate = make_decline_generator(
    reason_text="renaming is a multi-file refactor requiring cross-reference analysis",
)
