"""Declining patch generator for `cyclic-import` category."""

from __future__ import absolute_import

from scripts.quality.rollup_v2.patches._decline_helpers import make_decline_generator

GENERATOR_VERSION = "cyclic_import/1.0.0"
CATEGORY = "cyclic-import"
generate = make_decline_generator(
    reason_text="cyclic import resolution requires cross-file restructuring",
    suggested_tier="human-only",
    reason_code="cross-file-change",
)
