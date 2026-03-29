"""Coverage paths."""

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
    """Handle workspace relative path text."""
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
    """Handle candidate suffixes."""
    candidates: List[str] = []

    def _remember(value: str) -> None:
        """Handle remember."""
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
    """Handle existing repo file candidate."""
    candidate_text = str(normalized_path or "").strip().replace("\\", "/")
    if not candidate_text or candidate_text == ".":
        return ""

    first_existing = next((candidate for candidate in _candidate_suffixes(candidate_text) if Path(candidate).is_file()), "")
    return Path(first_existing).as_posix() if first_existing else ""


def _normalize_source_path(raw_path: str) -> str:
    """Handle normalize source path."""
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
    """Handle is ignored coverage source."""
    parts = PurePosixPath(normalized).parts
    return normalized.startswith(_IGNORED_SOURCE_PREFIXES) or any(part in _IGNORED_SOURCE_PARTS for part in parts)


def _should_track_coverage_source(source_path: str) -> bool:
    """Handle should track coverage source."""
    normalized = _normalize_source_path(source_path)
    return bool(normalized) and not _is_ignored_coverage_source(normalized) and Path(normalized).is_file()


def _coverage_source_candidates(raw_path: str, source_roots: List[str] | None = None) -> List[str]:
    """Handle coverage source candidates."""
    candidates: List[str] = []

    def _remember(value: str) -> None:
        """Handle remember."""
        normalized = _normalize_source_path(value)
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    _remember(raw_path)
    for source_root in source_roots or []:
        source_root_text = str(source_root or "").strip()
        if source_root_text:
            _remember(f"{source_root_text.rstrip('/')}/{str(raw_path or '').lstrip('./')}")
    return candidates
