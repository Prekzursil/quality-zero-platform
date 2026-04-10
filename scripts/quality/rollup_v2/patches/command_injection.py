"""Declining patch generator for `command-injection` category."""
from __future__ import absolute_import

from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "command_injection/1.0.0"
CATEGORY = "command-injection"


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Command injection fixes require context-aware sanitization; defer to LLM."""
    return PatchDeclined(
        reason_code="ambiguous-fix",
        reason_text="command injection fixes require context-aware input sanitization",
        suggested_tier="llm-fallback",
    )
