"""Deterministic patch generator for `print-in-production` category."""
from __future__ import absolute_import

import difflib
import re
from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "print_in_production/1.0.0"
CATEGORY = "print-in-production"

_PRINT_CALL = re.compile(r"^(\s*)print\((.+)\)\s*$")
_HAS_LOGGING_IMPORT = re.compile(r"^\s*import\s+logging\b", re.MULTILINE)


def generate(
    finding: Finding,
    source_file_content: str,
    _repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Replace `print(...)` with `logging.info(...)` and add import if needed."""
    lines = source_file_content.splitlines(keepends=True)
    target_index = finding.line - 1
    if target_index < 0 or target_index >= len(lines):
        return PatchDeclined(
            reason_code="provider-data-insufficient",
            reason_text=f"line {finding.line} out of range",
            suggested_tier="skip",
        )

    target_line = lines[target_index]
    match = _PRINT_CALL.match(target_line)
    if not match:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text=f"line {finding.line} is not a simple print() call",
            suggested_tier="llm-fallback",
        )

    indent = match.group(1)
    args = match.group(2)
    new_line = f"{indent}logging.info({args})\n"

    patched_lines = lines.copy()
    patched_lines[target_index] = new_line

    # Add `import logging` at the top if not already present
    if not _HAS_LOGGING_IMPORT.search(source_file_content):
        patched_lines.insert(0, "import logging\n")

    diff = "".join(difflib.unified_diff(
        lines,
        patched_lines,
        fromfile=f"a/{finding.file}",
        tofile=f"b/{finding.file}",
    ))
    return PatchResult(
        unified_diff=diff,
        confidence="medium",
        category=CATEGORY,
        generator_version=GENERATOR_VERSION,
        touches_files=frozenset({Path(finding.file)}),
    )
