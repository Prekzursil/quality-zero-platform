"""Declining patch generator for `cyclic-import` category."""
from __future__ import absolute_import

from pathlib import Path

from scripts.quality.rollup_v2.types.finding import Finding
from scripts.quality.rollup_v2.types.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "cyclic_import/1.0.0"
CATEGORY = "cyclic-import"


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Cyclic imports require cross-file restructuring; human-only."""
    return PatchDeclined(
        reason_code="cross-file-change",
        reason_text="cyclic import resolution requires cross-file restructuring",
        suggested_tier="human-only",
    )
