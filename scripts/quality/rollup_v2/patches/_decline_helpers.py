"""Shared factory for category-decline patch generators (Phase 9 §A.1.4).

Many patch categories (12 of them: command-injection, weak-crypto,
too-long, todo-comment, ...) cannot produce a deterministic auto-fix
and must always return ``PatchDeclined``. The 24-line copy-paste shape
of those modules was triggering qlty's duplicate-code smell at mass
~97 across 12 locations.

This module hosts the one-and-only ``generate`` body. Each declining
patch module reduces to a 5-line shim that publishes the same
module-level interface the dispatcher expects: ``CATEGORY``,
``GENERATOR_VERSION``, and a ``generate`` callable.
"""

from __future__ import absolute_import

from pathlib import Path
from typing import Callable, Literal

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GenerateFn = Callable[[Finding, str, Path], PatchResult | PatchDeclined | None]
SuggestedTier = Literal["llm-fallback", "human-only"]
ReasonCode = Literal["ambiguous-fix", "cross-file-change"]


def make_decline_generator(
    *,
    reason_text: str,
    suggested_tier: SuggestedTier = "llm-fallback",
    reason_code: ReasonCode = "ambiguous-fix",
) -> GenerateFn:
    """Build a ``generate(...)`` callable that always returns PatchDeclined.

    Parameters
    ----------
    reason_text:
        Human-readable explanation surfaced in the rollup report and
        used to brief the LLM-fallback prompt when ``suggested_tier``
        is ``"llm-fallback"``.
    suggested_tier:
        ``"llm-fallback"`` (default) routes the finding to the §A.4 LLM
        patch generator. ``"human-only"`` skips the LLM and parks the
        finding for human review (used for coverage gaps, cyclic
        imports, TODO comments).
    reason_code:
        ``"ambiguous-fix"`` (default) for category-specific decline.
        ``"cross-file-change"`` for cyclic-import (multi-file rewrite
        that the dispatcher's single-file flow cannot represent).
    """

    def generate(
        finding: Finding,  # noqa: ARG001 — interface contract; consumed by other generators
        source_file_content: str,  # noqa: ARG001
        repo_root: Path,  # noqa: ARG001
    ) -> PatchResult | PatchDeclined | None:
        """Return PatchDeclined with the configured tier + reason."""
        return PatchDeclined(
            reason_code=reason_code,
            reason_text=reason_text,
            suggested_tier=suggested_tier,
        )

    return generate
