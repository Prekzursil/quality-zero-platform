"""Canonical Finding dataclass (per design §3.1 + §A.3.2 + §A.4.1)."""
from __future__ import absolute_import

from dataclasses import dataclass
from typing import Final, Literal, Tuple

from scripts.quality.rollup_v2.types.corroborator import Corroborator

SCHEMA_VERSION: Final[str] = "qzp-finding/1"

CATEGORY_GROUP_SECURITY: Final[str] = "security"
CATEGORY_GROUP_QUALITY: Final[str] = "quality"
CATEGORY_GROUP_STYLE: Final[str] = "style"

_VALID_CATEGORY_GROUPS: Final[frozenset[str]] = frozenset(
    {CATEGORY_GROUP_SECURITY, CATEGORY_GROUP_QUALITY, CATEGORY_GROUP_STYLE}
)

_VALID_PATCH_SOURCES: Final[frozenset[str]] = frozenset({"deterministic", "llm", "none"})
_VALID_CORROBORATION: Final[frozenset[str]] = frozenset({"multi", "single"})


CategoryGroup = Literal["security", "quality", "style"]
PatchSource = Literal["deterministic", "llm", "none"]
Corroboration = Literal["multi", "single"]


@dataclass(frozen=True, slots=True)
class Finding:
    """Canonical finding produced by any provider normalizer.

    All string fields that may contain user/provider content have been passed
    through `redact_secrets()` by the normalizer before construction (see §B.1).
    """
    schema_version: str
    finding_id: str
    file: str
    line: int
    end_line: int
    column: int | None
    category: str
    category_group: CategoryGroup
    severity: str
    corroboration: Corroboration
    primary_message: str
    corroborators: Tuple[Corroborator, ...]
    fix_hint: str | None
    patch: str | None
    patch_source: PatchSource
    patch_confidence: str | None
    context_snippet: str
    source_file_hash: str
    cwe: str | None
    autofixable: bool
    tags: Tuple[str, ...]
    patch_error: str | None = None   # per A.6 — set when a patch generator raised; none otherwise

    def __post_init__(self) -> None:
        if self.category_group not in _VALID_CATEGORY_GROUPS:
            raise AssertionError(
                f"category_group must be one of {_VALID_CATEGORY_GROUPS}, got {self.category_group!r}"
            )
        if self.patch_source not in _VALID_PATCH_SOURCES:
            raise AssertionError(
                f"patch_source must be one of {_VALID_PATCH_SOURCES}, got {self.patch_source!r}"
            )
        if self.corroboration not in _VALID_CORROBORATION:
            raise AssertionError(
                f"corroboration must be one of {_VALID_CORROBORATION}, got {self.corroboration!r}"
            )
        if self.schema_version != SCHEMA_VERSION:
            raise AssertionError(
                f"schema_version must be {SCHEMA_VERSION!r}, got {self.schema_version!r}"
            )
