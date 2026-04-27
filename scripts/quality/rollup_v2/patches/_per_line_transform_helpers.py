"""Shared factory for "transform every line" deterministic patch generators.

The ``quote-style`` and ``tab-vs-space`` patch generators were 27-line
clones of each other (qlty mass = 144). Both apply a per-line
transform across the whole source, check whether the transform did
anything, and emit either a diff or a PatchDeclined.

Each per-category module reduces to a 9-line shim that imports the
factory, supplies the line-transform + no-change message + module
metadata, and re-exports ``generate``.
"""

from __future__ import absolute_import

import difflib
from pathlib import Path
from typing import Callable, Literal

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GenerateFn = Callable[[Finding, str, Path], PatchResult | PatchDeclined | None]
LineTransform = Callable[[str], str]
Confidence = Literal["high", "medium", "low"]


def make_per_line_transform_generator(
    *,
    line_transform: LineTransform,
    no_change_reason_text: str,
    category: str,
    generator_version: str,
    confidence: Confidence,
) -> GenerateFn:
    """Build a ``generate(...)`` callable that applies ``line_transform`` per line.

    Parameters
    ----------
    line_transform:
        Pure function applied to each source line. Should return the
        line unchanged if no transformation is needed; the helper
        compares the resulting list to the original to decide whether
        any change was made.
    no_change_reason_text:
        ``reason_text`` on the returned ``PatchDeclined`` when the
        transform was a no-op (e.g. ``"no leading tabs found"``).
        Suggested tier is ``"skip"`` since there's literally nothing
        to fix.
    category, generator_version, confidence:
        Per-category fields echoed onto the ``PatchResult``.
    """

    def generate(
        finding: Finding,
        source_file_content: str,
        repo_root: Path,  # noqa: ARG001 — interface contract; not consumed
    ) -> PatchResult | PatchDeclined | None:
        """Apply ``line_transform`` to every source line; emit a diff or decline."""
        lines = source_file_content.splitlines(keepends=True)
        patched_lines = [line_transform(line) for line in lines]
        if patched_lines == lines:
            return PatchDeclined(
                reason_code="ambiguous-fix",
                reason_text=no_change_reason_text,
                suggested_tier="skip",
            )
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
