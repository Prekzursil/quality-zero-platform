"""Declining patch generator for `duplicate-code` category."""
from __future__ import absolute_import

from pathlib import Path

from scripts.quality.rollup_v2.types.finding import Finding
from scripts.quality.rollup_v2.types.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "duplicate_code/1.0.0"
CATEGORY = "duplicate-code"


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Duplicate code extraction is a multi-file refactor; defer to LLM."""
    return PatchDeclined(
        reason_code="ambiguous-fix",
        reason_text="duplicate code extraction is a multi-file-capable refactor",
        suggested_tier="llm-fallback",
    )
