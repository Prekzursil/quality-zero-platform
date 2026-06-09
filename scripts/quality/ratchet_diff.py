#!/usr/bin/env python3
"""Git new-code (diff-scoped) detection for the Layer-1 ratchet gate.

This module is the cohesive ``git diff`` unit extracted from
``ratchet_gate.py``: it resolves the merge-base, reads the added line ranges
for ``base..head``, and decides whether a single finding lands on a
genuinely-added line (clean-as-you-code). Keeping it in its own module keeps
the gate's state machine focused and each file under the complexity ceiling.

The gate re-exports these names, so ``scripts.quality.ratchet_gate.<name>``
continues to resolve for callers and tests.
"""

from __future__ import absolute_import

import re
import shutil
import subprocess  # nosec B404 -- only invoked with a fixed git argv, no shell
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Set

# Resolve the absolute path to git at import time so the subprocess call below
# runs a verified executable rather than depending on the runtime PATH (Bandit
# B607 / Ruff S607). ``shutil.which`` returns ``None`` if ``git`` is not on
# PATH; fall back to the literal ``"git"`` so the call fails fast with a clear
# error rather than crashing on a NoneType exec. This mirrors the reviewed
# precedent in scripts/quality/rollup_v2/apply_deterministic_patches.py.
_GIT_EXECUTABLE: str = shutil.which("git") or "git"

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_DIFF_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")


class RatchetError(RuntimeError):
    """Raised when the gate cannot make a safe assertion (e.g. the new-code
    diff command failed). Fail-loud: a silently-empty diff would disable the
    clean-as-you-code check and let a 'fix 1 MINOR, add 1 BLOCKER' swap pass.
    """


def _run_git(args: Sequence[str], repo_dir: Path) -> str:
    """Run ``git`` in ``repo_dir`` and return stdout.

    Raises :class:`RatchetError` when git itself fails (non-zero exit or
    missing binary). Callers that want "no diff" semantics must check for an
    empty *base* up front -- a failed git command is NEVER silently treated
    as an empty diff (that would disable new-code detection).
    """
    try:
        # Safe by construction: shell=False (list argv), git resolved to an
        # absolute path at import via ``shutil.which``, and ``args`` are
        # internal literals. ``# nosec`` silences Bandit B603 and
        # ``# noqa: S603`` silences Ruff S603 -- both informational warnings
        # about every subprocess call regardless of safety posture.
        completed = subprocess.run(  # noqa: S603  # nosec
            [_GIT_EXECUTABLE, *args],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RatchetError(
            f"git {' '.join(args)} failed (exit {exc.returncode}): "
            f"{(exc.stderr or '').strip()}"
        ) from exc
    except OSError as exc:
        raise RatchetError(f"could not run git: {exc}") from exc
    return completed.stdout


def _resolve_diff_base(repo_dir: Path, base_ref: str, head_sha: str) -> str:
    """Resolve the merge-base of ``base_ref`` and ``head_sha``.

    Raises :class:`RatchetError` if the merge-base cannot be computed (e.g.
    the head SHA is not present in this checkout -- the classic PR
    merge-commit-vs-head-ref mismatch). Returns "" only when ``base_ref`` is
    empty (caller explicitly opted out of new-code detection).
    """
    if not base_ref:
        return ""
    merge_base = _run_git(["merge-base", base_ref, head_sha or "HEAD"],
                          repo_dir).strip()
    if not merge_base:
        raise RatchetError(
            f"empty merge-base for {base_ref}..{head_sha or 'HEAD'} -- the "
            f"checked-out repo at {repo_dir} likely does not contain head SHA "
            f"{head_sha!r}. Ensure checkout ref == --head-sha and fetch-depth: 0."
        )
    return merge_base


def added_line_ranges(repo_dir: Path, base_sha: str,
                      head_sha: str) -> Dict[str, Set[int]]:
    """Return ``{file_path: {added_line_numbers}}`` for ``base..head``.

    Uses ``git diff --unified=0`` so only genuinely added/changed lines are
    captured. The line numbers are HEAD-side (matching the finding line
    numbers, which are anchored to the rollup SHA == head). Raises
    :class:`RatchetError` if the diff command fails (never returns an empty
    dict to mask a broken diff).
    """
    if not base_sha:
        return {}
    diff = _run_git(
        [
            "diff", "--unified=0", "--no-color",
            f"{base_sha}..{head_sha or 'HEAD'}"
        ],
        repo_dir,
    )
    result: Dict[str, Set[int]] = {}
    current_file = ""
    for raw in diff.splitlines():
        file_match = _DIFF_FILE_RE.match(raw)
        if file_match:
            current_file = file_match.group(1)
            result.setdefault(current_file, set())
            continue
        hunk = _HUNK_RE.match(raw)
        if hunk and current_file:
            _record_added_lines(result[current_file], hunk)
    return result


def _record_added_lines(target: Set[int], hunk: "re.Match[str]") -> None:
    """Add the line numbers spanned by a unified-diff hunk header to ``target``.

    ``count == 0`` means a pure deletion at this point -> no added lines.
    """
    start = int(hunk.group(1))
    count = int(hunk.group(2)) if hunk.group(2) is not None else 1
    for offset in range(count):
        target.add(start + offset)


def _normalize_path(path: str) -> str:
    """Normalize a finding/diff path for comparison (strip leading ./, slashes)."""
    return path.replace("\\", "/").lstrip("./").strip()


def is_new_code_finding(finding: Mapping[str, Any],
                        added: Mapping[str, Set[int]]) -> bool:
    """Return True when a finding lands on an added line in the diff.

    Repo-level / manifest-level / non-positional findings (line <= 0, empty
    file, or Dependabot's synthetic line==1 manifest pointer) are NOT treated
    as new-code: they have no meaningful (file, line) anchor in the diff and
    are governed by the per-provider total ceiling only.
    """
    line = _finding_line(finding)
    file_path = _normalize_path(str(finding.get("file") or ""))
    if line <= 0 or not file_path:
        return False
    added_lines = added.get(file_path)
    if added_lines is not None:
        return line in added_lines
    return _suffix_new_code_match(file_path, line, added)


def _finding_line(finding: Mapping[str, Any]) -> int:
    """Return the finding's line number, or 0 when missing/unparseable."""
    try:
        return int(finding.get("line") or 0)
    except (TypeError, ValueError):
        return 0


def _suffix_new_code_match(file_path: str, line: int,
                           added: Mapping[str, Set[int]]) -> bool:
    """Match a finding against added lines via a path-component suffix.

    Handles rollup-vs-diff path prefix differences, e.g. "app/api.py" vs
    "apps/api/app/api.py". A plain str.endswith would false-match "api.py"
    against ".../myapi.py"; requiring a leading "/" on the suffix prevents that.
    """
    for diff_file, lines in added.items():
        if _suffix_path_match(diff_file, file_path):
            return line in lines
    return False


def _suffix_path_match(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` share a path-component-aligned suffix."""
    if a == b:
        return True
    longer, shorter = (a, b) if len(a) >= len(b) else (b, a)
    return longer.endswith("/" + shorter)
