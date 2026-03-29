"""Normalize and classify coverage source paths."""

from __future__ import absolute_import

import re
from pathlib import Path, PurePosixPath
from typing import List

_IGNORED_SOURCE_PREFIXES = ("build/", "dist/", "coverage/", "vendor/")
_IGNORED_SOURCE_PARTS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
}


def _workspace_relative_path_text(text: str, workspace_root: str) -> str:
    """Convert an absolute path into workspace-relative text when possible."""
    path_obj = Path(text)
    if not path_obj.is_absolute():
        return text

    resolved_path = path_obj.resolve(strict=False).as_posix().rstrip("/")
    prefix = f"{workspace_root}/"
    if resolved_path == workspace_root:
        return ""
    if resolved_path.startswith(prefix):
        return resolved_path[len(prefix) :]
    return resolved_path


def _candidate_suffixes(candidate_text: str) -> List[str]:
    """Return increasingly shorter candidate suffixes for a source path."""
    candidates: List[str] = []

    def _remember(value: str) -> None:
        """Record one cleaned candidate path if it is unique."""
        cleaned = str(value or "").strip().replace("\\", "/").lstrip("./")
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)

    _remember(candidate_text)
    _remember(candidate_text.removeprefix("repo/"))

    parts = PurePosixPath(candidate_text).parts
    for index in range(len(parts)):
        _remember("/".join(parts[index:]))
    return candidates


def _existing_repo_file_candidate(normalized_path: str) -> str:
    """Return the first existing repository file candidate for a path string."""
    candidate_text = str(normalized_path or "").strip().replace("\\", "/")
    if not candidate_text or candidate_text == ".":
        return ""

    first_existing = next(
        (
            candidate
            for candidate in _candidate_suffixes(candidate_text)
            if Path(candidate).is_file()
        ),
        "",
    )
    return Path(first_existing).as_posix() if first_existing else ""


def _normalize_source_path(raw_path: str) -> str:
    """Normalize a coverage source path into a repository-relative path."""
    text = str(raw_path or "").strip().replace("\\", "/")
    text = re.sub(r"/+", "/", text)
    if not text or text == ".":
        return ""

    workspace_root = Path.cwd().resolve(strict=False).as_posix().rstrip("/")
    text = _workspace_relative_path_text(text, workspace_root)
    normalized = text
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        return ""

    candidate = _existing_repo_file_candidate(normalized)
    return candidate or normalized


def _is_ignored_coverage_source(normalized: str) -> bool:
    """Return whether a normalized path belongs to an ignored coverage location."""
    parts = PurePosixPath(normalized).parts
    return normalized.startswith(_IGNORED_SOURCE_PREFIXES) or any(
        part in _IGNORED_SOURCE_PARTS for part in parts
    )


def _should_track_coverage_source(source_path: str) -> bool:
    """Return whether a source path should contribute to coverage tracking."""
    normalized = _normalize_source_path(source_path)
    return (
        bool(normalized)
        and not _is_ignored_coverage_source(normalized)
        and Path(normalized).is_file()
    )


def _coverage_source_candidates(
    raw_path: str,
    source_roots: List[str] | None = None,
) -> List[str]:
    """Build normalized candidate paths from raw coverage paths and roots."""
    candidates: List[str] = []

    def _remember(value: str) -> None:
        """Record one normalized coverage candidate if it is unique."""
        normalized = _normalize_source_path(value)
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    _remember(raw_path)
    for source_root in source_roots or []:
        source_root_text = str(source_root or "").strip()
        if source_root_text:
            _remember(
                f"{source_root_text.rstrip('/')}/"
                f"{str(raw_path or '').lstrip('./')}"
            )
    return candidates
