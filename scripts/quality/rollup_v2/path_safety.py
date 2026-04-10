"""Path safety wrapper for rollup_v2 (per design §A.2.3 + §B.2).

IMPORTANT: This wrapper performs path resolution (`.resolve(strict=False)`)
on both the candidate and the repo root BEFORE calling `_ensure_within_root`
from `scripts.quality.common`. `_ensure_within_root` itself is lexical-only
and is NOT modified in PR 1 (per design §B.2.3 — "reuses as-is"). All path
normalization for rollup_v2 happens here, keeping the change inside PR 1's
coverage scope (`scripts/quality/rollup_v2/` per A.3.3).
"""
from __future__ import absolute_import

from pathlib import Path

from scripts.quality.common import _ensure_within_root


class PathEscapedRootError(ValueError):
    """Raised when a finding's file path escapes the repo root."""


def validate_finding_file(finding_file: str, repo_root: Path) -> Path:
    """Validate a `Finding.file` value against `repo_root`.

    Resolves both the candidate and the repo root via `.resolve(strict=False)`
    before comparison, so symlink-escape, lexical `..`, and non-existent-path
    traversal attempts are all caught even though the underlying
    `_ensure_within_root` helper is lexical-only.

    Returns the resolved absolute path on success. Raises PathEscapedRootError
    on any failure (lexical escape, symlink escape, absolute path outside root,
    empty string).
    """
    if not finding_file:
        raise PathEscapedRootError("finding_file is empty")
    raw = Path(finding_file) if Path(finding_file).is_absolute() else (repo_root / finding_file)
    # Resolve BOTH sides before calling _ensure_within_root. This is where rollup_v2
    # gets its "strengthened" path check without touching scripts/quality/common.py.
    candidate = raw.resolve(strict=False)
    resolved_root = repo_root.resolve(strict=False)
    try:
        _ensure_within_root(candidate, resolved_root)
    except ValueError as exc:
        raise PathEscapedRootError(str(exc)) from exc
    return candidate
