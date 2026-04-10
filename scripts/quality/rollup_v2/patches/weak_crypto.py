"""Declining patch generator for `weak-crypto` category."""
from __future__ import absolute_import

from pathlib import Path

from scripts.quality.rollup_v2.types.finding import Finding
from scripts.quality.rollup_v2.types.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "weak_crypto/1.0.0"
CATEGORY = "weak-crypto"


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Crypto algorithm replacement requires context-aware analysis; defer to LLM."""
    return PatchDeclined(
        reason_code="ambiguous-fix",
        reason_text="crypto algorithm replacement requires context-aware analysis",
        suggested_tier="llm-fallback",
    )
