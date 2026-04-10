"""Declining patch generator for `naming-convention` category."""
from __future__ import absolute_import

from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "naming_convention/1.0.0"
CATEGORY = "naming-convention"


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Renaming is a multi-file refactor; defer to LLM fallback."""
    return PatchDeclined(
        reason_code="ambiguous-fix",
        reason_text="renaming is a multi-file refactor requiring cross-reference analysis",
        suggested_tier="llm-fallback",
    )
