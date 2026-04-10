"""Declining patch generator for `shadowed-builtin` category."""
from __future__ import absolute_import

from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "shadowed_builtin/1.0.0"
CATEGORY = "shadowed-builtin"


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Renaming shadowed builtins requires multi-reference analysis; defer to LLM."""
    return PatchDeclined(
        reason_code="ambiguous-fix",
        reason_text="renaming shadowed builtins requires multi-reference analysis",
        suggested_tier="llm-fallback",
    )
