"""Declining patch generator for `open-redirect` category."""
from __future__ import absolute_import

from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "open_redirect/1.0.0"
CATEGORY = "open-redirect"


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Open redirect fixes require context-aware URL validation; defer to LLM."""
    return PatchDeclined(
        reason_code="ambiguous-fix",
        reason_text="open redirect fixes require context-aware URL validation",
        suggested_tier="llm-fallback",
    )
