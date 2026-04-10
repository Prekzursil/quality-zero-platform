"""Declining patch generator for `insecure-random` category."""
from __future__ import absolute_import

from pathlib import Path

from scripts.quality.rollup_v2.types.finding import Finding
from scripts.quality.rollup_v2.types.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "insecure_random/1.0.0"
CATEGORY = "insecure-random"


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """random-to-secrets rewrite can be complex; defer to LLM fallback."""
    return PatchDeclined(
        reason_code="ambiguous-fix",
        reason_text="random-to-secrets rewrite requires context-aware analysis",
        suggested_tier="llm-fallback",
    )
