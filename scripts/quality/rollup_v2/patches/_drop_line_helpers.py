"""Shared factory for "drop a single line" deterministic patch generators.

The ``unused-import`` and ``unused-variable`` patch generators were
57-line clones of each other (qlty mass = 271). Both produce a unified
diff that drops the single source line referenced by the finding,
gated only by a per-category regex shape check on that line.

This module hosts the shared body. Each per-category module reduces to
a 9-line shim that imports the factory, supplies the regex + decline
message + module-level metadata, and re-exports ``generate``.
"""

from __future__ import absolute_import

import difflib
import re
from pathlib import Path
from typing import Callable, Literal

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GenerateFn = Callable[[Finding, str, Path], PatchResult | PatchDeclined | None]
Confidence = Literal["high", "medium", "low"]


def make_drop_line_generator(
    *,
    line_pattern: re.Pattern[str],
    decline_message: str,
    category: str,
    generator_version: str,
    confidence: Confidence,
) -> GenerateFn:
    """Build a ``generate(...)`` callable that drops one line on a regex match.

    Parameters
    ----------
    line_pattern:
        Compiled regex applied to the target line. The fix only fires
        when this regex matches the line in question.
    decline_message:
        Human-readable explanation surfaced when the regex does NOT
        match. Becomes the ``reason_text`` on the returned
        ``PatchDeclined`` (suggested_tier ``"llm-fallback"``).
    category:
        Patch category slug (e.g. ``"unused-import"``). Echoed onto
        the ``PatchResult.category`` field.
    generator_version:
        Per-category version string (e.g. ``"unused_import/1.0.0"``).
        Echoed onto the ``PatchResult.generator_version`` field.
    confidence:
        ``"high"`` / ``"medium"`` / ``"low"`` confidence label for the
        produced patch. Used by the dispatcher to decide whether to
        auto-merge.
    """

    def generate(
        finding: Finding,
        source_file_content: str,
        repo_root: Path,  # noqa: ARG001 — interface contract; not consumed by drop-line generators
    ) -> PatchResult | PatchDeclined | None:
        """Drop the single line at ``finding.line`` if it matches ``line_pattern``."""
        lines = source_file_content.splitlines(keepends=True)
        target_index = finding.line - 1
        if target_index < 0 or target_index >= len(lines):
            return PatchDeclined(
                reason_code="provider-data-insufficient",
                reason_text=f"line {finding.line} out of range",
                suggested_tier="skip",
            )

        target_line = lines[target_index]
        if not line_pattern.match(target_line):
            return PatchDeclined(
                reason_code="ambiguous-fix",
                reason_text=f"line {finding.line} {decline_message}",
                suggested_tier="llm-fallback",
            )

        patched_lines = lines.copy()
        patched_lines.pop(target_index)
        diff = "".join(
            difflib.unified_diff(
                lines,
                patched_lines,
                fromfile=f"a/{finding.file}",
                tofile=f"b/{finding.file}",
            )
        )
        return PatchResult(
            unified_diff=diff,
            confidence=confidence,
            category=category,
            generator_version=generator_version,
            touches_files=frozenset({Path(finding.file)}),
        )

    return generate
