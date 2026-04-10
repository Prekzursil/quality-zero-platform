"""Declining patch generator for `coverage-gap` category (Task 9.31)."""
from __future__ import absolute_import

from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "coverage_gap/1.0.0"
CATEGORY = "coverage-gap"


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Coverage gaps require human-written test cases; cannot be auto-patched."""
    return PatchDeclined(
        reason_code="ambiguous-fix",
        reason_text="coverage gaps require human-written test cases",
        suggested_tier="human-only",
    )
