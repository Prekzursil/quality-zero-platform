"""Declining patch generator for `weak-crypto` category."""
from __future__ import absolute_import

from scripts.quality.rollup_v2.patches._declining import make_decline_generator

GENERATOR_VERSION = "weak_crypto/1.0.0"
CATEGORY = "weak-crypto"

generate = make_decline_generator(
    reason_text="crypto algorithm replacement requires context-aware analysis",
    suggested_tier="llm-fallback",
)
