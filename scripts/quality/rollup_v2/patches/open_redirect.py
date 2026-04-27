"""Declining patch generator for `open-redirect` category."""
from __future__ import absolute_import

from scripts.quality.rollup_v2.patches._declining import make_decline_generator

GENERATOR_VERSION = "open_redirect/1.0.0"
CATEGORY = "open-redirect"

generate = make_decline_generator(
    reason_text="open redirect fixes require context-aware URL validation",
    suggested_tier="llm-fallback",
)
