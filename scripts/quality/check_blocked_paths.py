#!/usr/bin/env python3
"""Blocked-paths guard (single source of truth for the autofix safety chain).

The QRv2 autofix sweep plus its formatter-only verifier MUST refuse to touch
sensitive infrastructure files, so an auto-merge can never silently reformat a
workflow, a lockfile, or a build-config and slip it past review. This module
is the canonical implementation of that refusal: the workflow guard in
``.github/workflows/reusable-remediation-loop.yml`` (the "Post-remediation
blocked-paths check (§B.3.17)" step) hand-rolls a smaller subset; this module
supersedes it with the complete pattern set so every caller shares one list.

``BLOCKED_PATTERNS`` is that list. ``is_blocked_path(path)`` is the primitive
predicate; ``blocked_paths(paths)`` returns the blocked subset of an iterable.
A thin CLI (``main(argv)``) checks either the consumer repo's changed files
(``--base-ref REF`` → ``git diff --name-only <base-ref>``) or an explicit list
(``--paths p1 p2 ...``); it exits 1 (printing each offender) when any blocked
path is present, 0 when clean, and 2 on a usage or git error.

Matching policy: a path is blocked when its full POSIX form matches a pattern
OR its basename matches a pattern. The basename leg makes the bare-name
patterns (``pyproject.toml``, ``package-lock.json``, ``Cargo.lock`` ...)
depth-independent — the fail-safe direction for a security guard, since a
lockfile is sensitive wherever it lives. ``.github/**`` matches at any depth
because :func:`fnmatch.fnmatch` does not treat ``/`` as a separator (``*``
crosses path components), so the full-path leg already covers nested entries.
"""

from __future__ import absolute_import

import argparse
import shutil
import subprocess  # nosec B404
import sys
from fnmatch import fnmatch
from typing import Iterable, List, Optional, Sequence, Tuple

# Resolve the absolute path to git at import time so the subprocess call below
# runs a verified executable rather than depending on the runtime PATH (Bandit
# B607 / Ruff S607). ``shutil.which`` returns ``None`` when ``git`` is absent;
# fall back to the literal ``"git"`` so the call fails fast with a clear error
# rather than crashing on a ``NoneType`` exec.
_GIT_EXECUTABLE: str = shutil.which("git") or "git"

# The canonical blocked-path patterns. This is the single source of truth for
# the autofix sweep, the formatter-only verifier, and the workflow guard. Each
# entry is an ``fnmatch`` glob evaluated against both the full POSIX path and
# the basename (see module docstring). Sourced from the workflow guard's
# ``BLOCKED_PATTERNS`` array (the eight infra patterns) plus the broader set
# the focused review requires the SSOT to cover (lockfiles, build configs,
# git attributes, and Windows scripts).
BLOCKED_PATTERNS: Tuple[str, ...] = (
    ".github/**",
    "Dockerfile",
    "Dockerfile.*",
    "docker-compose*.yml",
    "requirements*.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    ".npmrc",
    ".pip/pip.conf",
    "package-lock.json",
    "Cargo.lock",
    "poetry.lock",
    "*.gitattributes",
    "*.bat",
    "*.ps1",
)


class BlockedPathsError(RuntimeError):
    """Raised when the underlying ``git diff`` invocation fails."""


def _normalize(path: str) -> str:
    """Return ``path`` with POSIX separators, trimmed, and no ``./`` prefix."""
    normalized = path.replace("\\", "/").strip()
    return normalized.removeprefix("./")


def is_blocked_path(path: str) -> bool:
    """Return True when ``path`` matches a blocked pattern.

    Separators are normalized to ``/`` first. Each pattern is matched against
    both the full path and the basename so bare-name patterns are
    depth-independent (see the module docstring for the rationale).
    """
    normalized = _normalize(path)
    if not normalized:
        return False
    basename = normalized.rsplit("/", 1)[-1]
    return any(
        fnmatch(normalized, pattern) or fnmatch(basename, pattern)
        for pattern in BLOCKED_PATTERNS)


def blocked_paths(paths: Iterable[str]) -> List[str]:
    """Return the subset of ``paths`` that are blocked, preserving order."""
    return [path for path in paths if is_blocked_path(path)]


def _changed_files(base_ref: str, repo_dir: str) -> List[str]:
    """Return files changed vs ``base_ref`` in ``repo_dir`` via ``git diff``.

    Raises :class:`BlockedPathsError` when ``git`` exits nonzero (e.g. an
    unknown ref), so a broken diff fails loud instead of silently reporting an
    empty — and therefore "clean" — change set.
    """
    # Safe by construction: shell=False (list argv), git resolved to an
    # absolute path via ``shutil.which`` at import, and the only dynamic token
    # is a user-supplied ref passed as a plain argument (never interpolated).
    # ``# nosec`` silences Bandit B603 and ``# noqa: S603`` silences Ruff S603,
    # both informational warnings about every subprocess call.
    result = subprocess.run(  # noqa: S603  # nosec
        [_GIT_EXECUTABLE, "diff", "--no-renames", "--name-only", base_ref],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or "git diff failed"
        raise BlockedPathsError(message)
    return result.stdout.splitlines()


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        description="Refuse autofix changes to sensitive infrastructure paths.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--base-ref",
        help="Diff the consumer repo's changed files against this git ref.",
    )
    source.add_argument(
        "--paths",
        nargs="+",
        help="Explicit paths to check instead of diffing a git ref.",
    )
    parser.add_argument(
        "--repo-dir",
        default=".",
        help="Working tree to run git diff in (only with --base-ref).",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint: return 0 (clean), 1 (blocked present), or 2 (error)."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.base_ref is not None:
        try:
            candidates = _changed_files(args.base_ref, args.repo_dir)
        except BlockedPathsError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
    else:
        candidates = list(args.paths)
    offenders = blocked_paths(candidates)
    if offenders:
        for offender in offenders:
            print(offender)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
