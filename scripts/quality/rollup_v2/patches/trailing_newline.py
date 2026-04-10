"""Deterministic patch generator for `trailing-newline` category."""
from __future__ import absolute_import

import difflib
from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "trailing_newline/1.0.0"
CATEGORY = "trailing-newline"


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Ensure file ends with exactly one newline."""
    if source_file_content.endswith("\n") and not source_file_content.endswith("\n\n"):
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="file already ends with exactly one newline",
            suggested_tier="skip",
        )
    patched = source_file_content.rstrip("\n") + "\n"
    lines = source_file_content.splitlines(keepends=True)
    patched_lines = patched.splitlines(keepends=True)
    diff = "".join(difflib.unified_diff(
        lines,
        patched_lines,
        fromfile=f"a/{finding.file}",
        tofile=f"b/{finding.file}",
    ))
    if not diff:  # pragma: no cover -- rstrip+newline always differs from original when precondition met
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="no trailing newline change needed",
            suggested_tier="skip",
        )
    return PatchResult(
        unified_diff=diff,
        confidence="high",
        category=CATEGORY,
        generator_version=GENERATOR_VERSION,
        touches_files=frozenset({Path(finding.file)}),
    )
