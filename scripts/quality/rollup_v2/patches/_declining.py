"""Helper for declining-only patch generators (per design §5.1).

Every category that legitimately requires human judgement or LLM fallback shares
the exact same generator skeleton: declare ``CATEGORY``/``GENERATOR_VERSION`` and
return a single ``PatchDeclined`` instance from ``generate()``. Hand-rolling that
skeleton in 12 files generates a 24-line duplication block flagged by qlty.

This factory builds the closure once per category so each patch module collapses
to a 3-line declaration: import, version/category constants, and one factory call.
"""
from __future__ import absolute_import

from pathlib import Path
from typing import Callable

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

DeclineGenerator = Callable[[Finding, str, Path], PatchResult | PatchDeclined | None]


def make_decline_generator(
    *,
    reason_text: str,
    suggested_tier: str,
    reason_code: str = "ambiguous-fix",
) -> DeclineGenerator:
    """Return a generator that always declines with the given reason and tier.

    ``reason_code`` defaults to ``"ambiguous-fix"`` (the most common case);
    callers like ``cyclic_import`` override it to ``"cross-file-change"``.
    """
    declined = PatchDeclined(
        reason_code=reason_code,
        reason_text=reason_text,
        suggested_tier=suggested_tier,
    )

    def generate(
        finding: Finding,
        source_file_content: str,
        repo_root: Path,
    ) -> PatchResult | PatchDeclined | None:
        return declined

    return generate
