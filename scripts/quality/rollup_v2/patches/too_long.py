"""Declining patch generator for `too-long` category."""
from __future__ import absolute_import

from pathlib import Path

from scripts.quality.rollup_v2.types.finding import Finding
from scripts.quality.rollup_v2.types.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "too_long/1.0.0"
CATEGORY = "too-long"


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Function/file length reduction requires extract-method refactoring; defer to LLM."""
    return PatchDeclined(
        reason_code="ambiguous-fix",
        reason_text="length reduction requires extract-method refactoring",
        suggested_tier="llm-fallback",
    )
