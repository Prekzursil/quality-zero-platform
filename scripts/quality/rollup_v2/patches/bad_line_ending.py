"""Deterministic patch generator for `bad-line-ending` category."""
from __future__ import absolute_import

import difflib
from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "bad_line_ending/1.0.0"
CATEGORY = "bad-line-ending"


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    r"""Convert \\r\\n line endings to \\n."""
    if "\r\n" not in source_file_content:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="no CRLF line endings found",
            suggested_tier="skip",
        )
    patched = source_file_content.replace("\r\n", "\n")
    lines = source_file_content.splitlines(keepends=True)
    patched_lines = patched.splitlines(keepends=True)
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
