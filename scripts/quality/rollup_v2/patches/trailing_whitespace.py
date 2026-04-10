"""Deterministic patch generator for `trailing-whitespace` category."""
from __future__ import absolute_import

import difflib
import re
from pathlib import Path

from scripts.quality.rollup_v2.types.finding import Finding
from scripts.quality.rollup_v2.types.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "trailing_whitespace/1.0.0"
CATEGORY = "trailing-whitespace"

_TRAILING_WS = re.compile(r"[ \t]+(?=\r?\n|$)")


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Strip trailing whitespace from every line."""
    lines = source_file_content.splitlines(keepends=True)
    patched_lines = [_TRAILING_WS.sub("", line) for line in lines]
    if patched_lines == lines:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="no trailing whitespace found",
            suggested_tier="skip",
        )
    diff = "".join(difflib.unified_diff(
        lines,
        patched_lines,
        fromfile=f"a/{finding.file}",
        tofile=f"b/{finding.file}",
    ))
    return PatchResult(
        unified_diff=diff,
        confidence="high",
        category=CATEGORY,
        generator_version=GENERATOR_VERSION,
        touches_files=frozenset({Path(finding.file)}),
    )
