"""Declining patch generator for `insecure-random` category."""
from __future__ import absolute_import

from scripts.quality.rollup_v2.patches._declining import make_decline_generator

GENERATOR_VERSION = "insecure_random/1.0.0"
CATEGORY = "insecure-random"

generate = make_decline_generator(
    reason_text="random-to-secrets rewrite requires context-aware analysis",
    suggested_tier="llm-fallback",
)
