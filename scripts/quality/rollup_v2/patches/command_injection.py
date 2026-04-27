"""Declining patch generator for `command-injection` category."""
from __future__ import absolute_import

from scripts.quality.rollup_v2.patches._declining import make_decline_generator

GENERATOR_VERSION = "command_injection/1.0.0"
CATEGORY = "command-injection"

generate = make_decline_generator(
    reason_text="command injection fixes require context-aware input sanitization",
    suggested_tier="llm-fallback",
)
