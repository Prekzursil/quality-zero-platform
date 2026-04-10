"""Patch result/declined types (per design §A.1.3 + §B.3.11).

Per Round 3 Designer feedback: no Protocol class for the generator interface.
The dispatcher calls `gen.generate(finding, source_file_content=..., repo_root=...)`
where `gen` is a module exposing a module-level `generate` function.
"""
from __future__ import absolute_import

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

PatchDeclinedReason = Literal[
    "requires-ast-rewrite",
    "cross-file-change",
    "ambiguous-fix",
    "provider-data-insufficient",
    "path-traversal-rejected",
]

PatchConfidence = Literal["high", "medium", "low"]
PatchSuggestedTier = Literal["llm-fallback", "human-only", "skip"]

_VALID_REASONS: Final[frozenset[str]] = frozenset(
    {
        "requires-ast-rewrite",
        "cross-file-change",
        "ambiguous-fix",
        "provider-data-insufficient",
        "path-traversal-rejected",
    }
)
_VALID_CONFIDENCE: Final[frozenset[str]] = frozenset({"high", "medium", "low"})
_VALID_TIERS: Final[frozenset[str]] = frozenset({"llm-fallback", "human-only", "skip"})


@dataclass(frozen=True, slots=True)
class PatchResult:
    """Result of a successful deterministic patch generation."""

    unified_diff: str
    confidence: PatchConfidence
    category: str
    generator_version: str
    touches_files: frozenset[Path]

    def __post_init__(self) -> None:
        if self.confidence not in _VALID_CONFIDENCE:
            raise AssertionError(f"invalid confidence: {self.confidence!r}")
        if not self.touches_files:
            raise AssertionError("touches_files must be non-empty")
        for p in self.touches_files:
            if not isinstance(p, Path):
                raise AssertionError(
                    f"touches_files must contain Path, got {type(p).__name__}"
                )


@dataclass(frozen=True, slots=True)
class PatchDeclined:
    """Returned when a generator decides it cannot produce a patch."""

    reason_code: PatchDeclinedReason
    reason_text: str
    suggested_tier: PatchSuggestedTier

    def __post_init__(self) -> None:
        if self.reason_code not in _VALID_REASONS:
            raise AssertionError(f"invalid reason_code: {self.reason_code!r}")
        if self.suggested_tier not in _VALID_TIERS:
            raise AssertionError(f"invalid suggested_tier: {self.suggested_tier!r}")
