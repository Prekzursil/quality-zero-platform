"""Deterministic patch generator for `missing-docstring` category."""
from __future__ import absolute_import

import difflib
import re
from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "missing_docstring/1.0.0"
CATEGORY = "missing-docstring"

_DEF_OR_CLASS = re.compile(r"^(\s*)(def|class)\s+(\w+)")


def generate(
    finding: Finding,
    source_file_content: str,
    _repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Insert a TODO docstring as the first statement of a function/class."""
    lines = source_file_content.splitlines(keepends=True)
    target_index = finding.line - 1
    if target_index < 0 or target_index >= len(lines):
        return PatchDeclined(
            reason_code="provider-data-insufficient",
            reason_text=f"line {finding.line} out of range",
            suggested_tier="skip",
        )

    target_line = lines[target_index]
    match = _DEF_OR_CLASS.match(target_line)
    if not match:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text=f"line {finding.line} is not a def/class statement",
            suggested_tier="llm-fallback",
        )

    indent = match.group(1)
    body_indent = indent + "    "
    docstring_line = f'{body_indent}"""TODO: document."""\n'

    # Find the line after the def/class (the colon may be on the same line or next)
    # Look for the colon-terminated line, then insert after it
    insert_index = target_index + 1
    # If the def line itself contains ':', insert right after
    if ":" in target_line.split("#")[0]:  # Ignore colons in comments
        pass  # insert_index is already correct
    else:
        # Multi-line def -- find the closing '):' line
        while insert_index < len(lines):
            if ":" in lines[insert_index].split("#")[0]:
                insert_index += 1
                break
            insert_index += 1

    patched_lines = lines.copy()
    patched_lines.insert(insert_index, docstring_line)

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
