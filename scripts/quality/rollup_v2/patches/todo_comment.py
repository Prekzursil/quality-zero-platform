"""Declining patch generator for `todo-comment` category."""
from __future__ import absolute_import

from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "todo_comment/1.0.0"
CATEGORY = "todo-comment"


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """TODO comments require human judgment to resolve."""
    return PatchDeclined(
        reason_code="ambiguous-fix",
        reason_text="TODO comments require human judgment to resolve",
        suggested_tier="human-only",
    )
