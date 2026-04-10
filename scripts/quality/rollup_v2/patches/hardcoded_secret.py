"""Deterministic patch generator for `hardcoded-secret` category."""
from __future__ import absolute_import

import difflib
import re
from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "hardcoded_secret/1.0.0"
CATEGORY = "hardcoded-secret"

# Matches `SECRET_NAME = "value"` or `secret_name = 'value'`
_SECRET_ASSIGN = re.compile(
    r"""^(\s*)([A-Za-z_]\w*(?:_?(?:KEY|TOKEN|SECRET|PASSWORD|PASS|PWD|API_KEY|ACCESS_TOKEN))\s*=\s*)(['"])(.+?)\3""",
    re.IGNORECASE,
)


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Replace hardcoded secret with os.environ placeholder."""
    lines = source_file_content.splitlines(keepends=True)
    target_index = finding.line - 1
    if target_index < 0 or target_index >= len(lines):
        return PatchDeclined(
            reason_code="provider-data-insufficient",
            reason_text=f"line {finding.line} out of range",
            suggested_tier="skip",
        )

    target_line = lines[target_index]
    match = _SECRET_ASSIGN.match(target_line)
    if not match:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text=f"line {finding.line} does not match hardcoded secret pattern",
            suggested_tier="llm-fallback",
        )

    indent = match.group(1)
    var_assign = match.group(2)
    # Extract variable name for env var lookup
    var_name = var_assign.split("=")[0].strip().upper()
    new_line = f'{indent}{var_assign}os.environ["{var_name}"]  # TODO: load from secret manager\n'

    patched_lines = lines.copy()
    patched_lines[target_index] = new_line

    # Add `import os` at the top if not already present
    has_os_import = any(
        re.match(r"^\s*import\s+os\b", l) for l in lines
    )
    if not has_os_import:
        patched_lines.insert(0, "import os\n")

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
