"""Deterministic patch generator for `unused-import` category."""
from __future__ import absolute_import

import difflib
import re
from pathlib import Path

from scripts.quality.rollup_v2.types.finding import Finding
from scripts.quality.rollup_v2.types.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "unused_import/1.0.0"
CATEGORY = "unused-import"

# Matches `import X` or `from X import Y` lines
_IMPORT_LINE = re.compile(r"^\s*(import\s+\S+|from\s+\S+\s+import\s+\S+)")


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Remove the unused import on the finding line."""
    lines = source_file_content.splitlines(keepends=True)
    target_index = finding.line - 1
    if target_index < 0 or target_index >= len(lines):
        return PatchDeclined(
            reason_code="provider-data-insufficient",
            reason_text=f"line {finding.line} out of range",
            suggested_tier="skip",
        )

    target_line = lines[target_index]
    if not _IMPORT_LINE.match(target_line):
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text=f"line {finding.line} does not look like an import statement",
            suggested_tier="llm-fallback",
        )

    patched_lines = lines.copy()
    patched_lines.pop(target_index)

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
