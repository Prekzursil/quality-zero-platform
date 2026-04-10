"""Deterministic patch generator for `assert-in-production` category."""
from __future__ import absolute_import

import difflib
import re
from pathlib import Path

from scripts.quality.rollup_v2.types.finding import Finding
from scripts.quality.rollup_v2.types.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "assert_in_production/1.0.0"
CATEGORY = "assert-in-production"

# Matches `assert <expr>, <msg>` or `assert <expr>`
_ASSERT_WITH_MSG = re.compile(r"^(\s*)assert\s+(.+?),\s*(.+?)\s*$")
_ASSERT_NO_MSG = re.compile(r"^(\s*)assert\s+(.+?)\s*$")


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Replace `assert X, msg` with `if not X: raise AssertionError(msg)`."""
    lines = source_file_content.splitlines(keepends=True)
    target_index = finding.line - 1
    if target_index < 0 or target_index >= len(lines):
        return PatchDeclined(
            reason_code="provider-data-insufficient",
            reason_text=f"line {finding.line} out of range",
            suggested_tier="skip",
        )

    target_line = lines[target_index]
    match_msg = _ASSERT_WITH_MSG.match(target_line)
    match_no_msg = _ASSERT_NO_MSG.match(target_line)

    if match_msg:
        indent = match_msg.group(1)
        condition = match_msg.group(2)
        msg = match_msg.group(3)
        new_line = f"{indent}if not ({condition}):\n{indent}    raise AssertionError({msg})\n"
    elif match_no_msg:
        indent = match_no_msg.group(1)
        condition = match_no_msg.group(2)
        new_line = f"{indent}if not ({condition}):\n{indent}    raise AssertionError({condition!r})\n"
    else:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text=f"line {finding.line} is not a simple assert statement",
            suggested_tier="llm-fallback",
        )

    patched_lines = lines.copy()
    patched_lines[target_index] = new_line

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
