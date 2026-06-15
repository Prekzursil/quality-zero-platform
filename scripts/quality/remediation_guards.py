#!/usr/bin/env python3
"""Remediation-lane safety guards (campaign charter §5).

The autonomous remediation/backlog lane once force-pushed a fixture scaffold
tree (``example.py`` / ``init`` commits authored ``Test <test@test.com>``)
over live PR branches: fixture tests inherited ``GIT_DIR``/``GIT_WORK_TREE``
from a git hook and mutated the real repository, after which the lane's PR
action committed and force-pushed whatever was left of the working tree.
This module is the chokepoint every remediation candidate MUST pass before
any push or PR creation:

* :func:`assert_no_force_push` — refuse ``--force``/``--force-with-lease``/
  ``+refspec``/delete-refspec push arguments outright.
* :func:`assert_tree_not_shrunk` — refuse candidates that delete more than
  ``max_deletion_ratio`` of the tracked files vs base (the scaffold-clobber
  signature: 723 files collapsing to 1).
* :func:`assert_diff_is_safe_class` — validate that a candidate diff is a
  mechanically-safe class (whitespace / line-wrap / import-order /
  quote-style). Conservative by design: anything unparseable, structural
  (file adds/deletes/renames/mode changes/binary), or not provably safe is
  rejected.
* :func:`assert_not_blocked_path` — refuse changes to sensitive
  infrastructure paths. Delegates to the SSOT
  ``scripts.quality.check_blocked_paths`` when importable (PR #241) and
  otherwise enforces the same ``BLOCKED_PATTERNS`` contract locally.
* :func:`assert_security_class_pr_only` — delegate to
  ``scripts.quality.security_class_guard.ensure_pr_only_for_security`` so a
  security-class finding can never ride an auto-merge lane.

The CLI (``main``) is the guard-runner entry point the remediation and
backlog workflows call between the Codex execution step and PR creation. It
exits 0 when every guard passes, 1 on a guard violation, and 2 on an
execution/configuration error. Every git subprocess runs with the
``GIT_DIR``/``GIT_WORK_TREE``/``GIT_INDEX_FILE`` redirection variables
scrubbed from the environment so a hook-injected ``GIT_DIR`` can never make
this module operate on the wrong repository again.
"""

from __future__ import absolute_import

import argparse
import importlib
import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = str(Path(__file__).resolve().parents[2])
    if platform_root not in sys.path:
        sys.path.insert(0, platform_root)


_ensure_platform_on_syspath()

from scripts.quality import security_class_guard  # noqa: E402

# Resolve git once at import so the subprocess call below runs a verified
# executable rather than depending on the runtime PATH (Bandit B607).
_GIT_EXECUTABLE: str = shutil.which("git") or "git"

# Hook-injected git redirection variables. The scaffold-clobber incident
# happened because fixture git commands inherited these from a pre-push hook
# and silently operated on the REAL repository. Scrub them from every git
# subprocess this module spawns.
_GIT_ENV_BLOCKLIST: Tuple[str, ...] = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
)

# The bot identity the lane must commit/push as. NEVER the leaked fixture
# identity ``Test <test@test.com>``.
REMEDIATION_BOT_NAME = "qzp-remediation-bot"
REMEDIATION_BOT_EMAIL = "qzp-remediation@users.noreply.github.com"

# Push argv tokens that force-update, delete, or prune remote refs. Any of
# these in a remediation push is an immediate refusal.
_FORBIDDEN_PUSH_TOKENS: Tuple[str, ...] = (
    "-f",
    "--force",
    "--force-with-lease",
    "--force-if-includes",
    "--mirror",
    "--delete",
    "-d",
    "--prune",
)

# Local mirror of the SSOT contract in scripts/quality/check_blocked_paths.py
# (PR #241). Used only when that module is not importable; each entry is an
# fnmatch glob evaluated against both the full POSIX path and the basename.
_FALLBACK_BLOCKED_PATTERNS: Tuple[str, ...] = (
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

# Diff header lines that prove the change is structural rather than a
# formatter pass: file creation/deletion, renames/copies, and mode changes.
_UNSAFE_HEADER_PREFIXES: Tuple[str, ...] = (
    "new file mode",
    "deleted file mode",
    "old mode",
    "new mode",
    "rename from",
    "rename to",
    "copy from",
    "copy to",
    "similarity index",
    "dissimilarity index",
    "Binary files",
    "GIT binary patch",
)

_WORD_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]")
# Match single- or double-quoted string literals. Two mutually-exclusive
# branches per quote type — a body char is either a non-quote/non-backslash
# char ``[^'\\]`` / ``[^"\\]`` or an escaped pair ``\\.``; the alternatives
# cannot match the same character, so there is no ambiguous backtracking
# (clears CodeQL py/redos). Behaviour is identical to the previous
# backreference form, including allowing the opposite quote inside a literal.
_QUOTED_SEGMENT_RE = re.compile(r"(?:'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")")
_IMPORT_LINE_RE = re.compile(r"^\s*(import\s+\S+|from\s+\S+\s+import\s+\S.*)$")


class RemediationGuardError(RuntimeError):
    """Raised when a remediation candidate violates a safety guard."""


class GuardExecutionError(RuntimeError):
    """Raised when a guard cannot run (git failure, bad configuration)."""


@dataclass
class _HunkChange:
    """Removed/added line payloads of one unified-diff hunk."""

    removed: List[str]
    added: List[str]


@dataclass
class _FileChange:
    """All hunks of one file inside a unified diff."""

    header: str
    hunks: List[_HunkChange]


def _scrubbed_git_env() -> Dict[str, str]:
    """Return the inherited environment minus git redirection variables."""
    return {key: value for key, value in os.environ.items() if key not in _GIT_ENV_BLOCKLIST}


def _run_git(repo_root: str, args: Sequence[str]) -> str:
    """Run git in ``repo_root`` and return stdout, failing loud on errors."""
    # Safe by construction: shell=False (list argv), git resolved to an
    # absolute path via ``shutil.which`` at import, fixed flags plus refs
    # passed as plain arguments, and a scrubbed environment so hook-injected
    # GIT_DIR redirection cannot retarget the call.
    result = subprocess.run(  # noqa: S603  # nosec B603
        [_GIT_EXECUTABLE, "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
        env=_scrubbed_git_env(),
    )
    if result.returncode != 0:
        message = result.stderr.strip() or f"git {' '.join(args)} failed"
        raise GuardExecutionError(message)
    return result.stdout


def _nonempty_lines(text: str) -> List[str]:
    """Split ``text`` into stripped-of-blank lines, preserving order."""
    return [line for line in text.splitlines() if line.strip()]


def _assert_push_token_safe(token: str) -> None:
    """Raise :class:`RemediationGuardError` when one push token is forbidden."""
    if token in _FORBIDDEN_PUSH_TOKENS or token.startswith("--force-with-lease="):
        raise RemediationGuardError(
            f"forbidden push argument {token!r}: the remediation lane must never force-push, delete, or prune refs"
        )
    if token.startswith("+"):
        raise RemediationGuardError(
            f"forbidden refspec {token!r}: '+' refspecs force-update the remote ref"
        )
    if not token.startswith("-") and ":" in token and not token.split(":", 1)[0]:
        raise RemediationGuardError(
            f"forbidden refspec {token!r}: an empty-source refspec deletes the remote branch"
        )


def assert_no_force_push(args: Iterable[str]) -> None:
    """Refuse any push argv containing force/delete/prune semantics.

    ``args`` is the full argv (or tail) of an intended ``git push``. The
    guard rejects ``-f``/``--force``, ``--force-with-lease`` (bare or
    ``=ref`` form), ``--force-if-includes``, ``--mirror``, ``--delete``/
    ``-d``, ``--prune``, ``+refspec`` force-updates, and ``:refspec``
    remote-branch deletions.
    """
    for token in args:
        _assert_push_token_safe(str(token))


def assert_tree_not_shrunk(
    repo_root: str,
    base_ref: str,
    head_ref: Optional[str] = None,
    max_deletion_ratio: float = 0.5,
) -> None:
    """Refuse candidates that delete most of the tracked tree vs ``base_ref``.

    Compares the tracked files at ``base_ref`` against the deletions reported
    by ``git diff --diff-filter=D`` between ``base_ref`` and ``head_ref``
    (or the working tree when ``head_ref`` is ``None``). A deletion ratio
    above ``max_deletion_ratio`` is the scaffold-clobber signature (723
    tracked files collapsing to 1) and raises
    :class:`RemediationGuardError`.
    """
    base_files = _nonempty_lines(_run_git(repo_root, ["ls-tree", "-r", "--name-only", base_ref]))
    if not base_files:
        return
    diff_args = ["diff", "--no-renames", "--name-only", "--diff-filter=D", base_ref]
    if head_ref:
        diff_args.append(head_ref)
    deleted = _nonempty_lines(_run_git(repo_root, diff_args))
    ratio = len(deleted) / len(base_files)
    if ratio > max_deletion_ratio:
        raise RemediationGuardError(
            f"tree-shrink refused: candidate deletes {len(deleted)} of {len(base_files)} tracked files "
            f"(ratio {ratio:.2f} > limit {max_deletion_ratio:.2f}) — scaffold-clobber signature"
        )


def _consume_file_header_line(file_change: _FileChange, line: str) -> None:
    """Validate one pre-hunk header line, rejecting structural changes."""
    if line.startswith(("--- ", "+++ ")):
        if "/dev/null" in line:
            raise RemediationGuardError(
                f"file creation/deletion in diff ({file_change.header}): never formatter-only"
            )
        return
    if line.startswith(_UNSAFE_HEADER_PREFIXES):
        raise RemediationGuardError(
            f"structural change in diff ({file_change.header}): {line.strip()!r} is never formatter-only"
        )
    if line.startswith("index ") or not line.strip():
        return
    raise RemediationGuardError(
        f"unrecognized diff header line {line!r} ({file_change.header}); conservative reject"
    )


def _consume_hunk_line(file_change: _FileChange, line: str) -> None:
    """Record one in-hunk line into the file's latest hunk."""
    hunk = file_change.hunks[-1]
    if line.startswith("+"):
        hunk.added.append(line[1:])
        return
    if line.startswith("-"):
        hunk.removed.append(line[1:])
        return
    if line.startswith((" ", "\\")) or line == "":
        return
    raise RemediationGuardError(
        f"unparseable diff line {line!r} ({file_change.header}); conservative reject"
    )


def _consume_diff_line(files: List[_FileChange], line: str) -> None:
    """Feed one raw diff line into the per-file parse state."""
    if line.startswith("diff --git "):
        files.append(_FileChange(header=line[len("diff --git "):], hunks=[]))
        return
    if not files:
        if line.strip():
            raise RemediationGuardError(
                f"unparseable diff: content before the first file header ({line!r})"
            )
        return
    current = files[-1]
    if line.startswith("@@"):
        current.hunks.append(_HunkChange(removed=[], added=[]))
        return
    if current.hunks:
        _consume_hunk_line(current, line)
        return
    _consume_file_header_line(current, line)


def _parse_diff_files(diff_text: str) -> List[_FileChange]:
    """Parse a unified diff into per-file hunk changes, rejecting structure."""
    files: List[_FileChange] = []
    for line in diff_text.splitlines():
        _consume_diff_line(files, line)
    return files


def _code_tokens(lines: Sequence[str]) -> List[str]:
    """Tokenize ``lines`` into identifier runs and single symbols, dropping whitespace."""
    return _WORD_TOKEN_RE.findall("\n".join(lines))


def _quoted_segments(lines: Sequence[str]) -> List[str]:
    """Return the quoted string literals of ``lines`` in order."""
    return [match.group(0) for match in _QUOTED_SEGMENT_RE.finditer("\n".join(lines))]


def _is_whitespace_only_change(removed: Sequence[str], added: Sequence[str]) -> bool:
    """Check that removed/added differ only in whitespace or line wrapping.

    Token-stream equality keeps identifier boundaries intact (``a b`` ->
    ``ab`` is rejected), and quoted-segment equality rejects whitespace
    edits inside string literals, which would change behavior.
    """
    if _code_tokens(removed) != _code_tokens(added):
        return False
    return _quoted_segments(removed) == _quoted_segments(added)


def _is_import_order_only_change(removed: Sequence[str], added: Sequence[str]) -> bool:
    """Check that the hunk purely reorders import statements."""
    removed_imports = [line.strip() for line in removed if line.strip()]
    added_imports = [line.strip() for line in added if line.strip()]
    if not removed_imports or not added_imports:
        return False
    if not all(_IMPORT_LINE_RE.match(line) for line in removed_imports + added_imports):
        return False
    return sorted(removed_imports) == sorted(added_imports)


def _is_quote_style_only_change(removed: Sequence[str], added: Sequence[str]) -> bool:
    """Check that each changed line differs only in quote characters."""
    if not removed or len(removed) != len(added):
        return False
    return all(
        old.replace("'", '"') == new.replace("'", '"')
        for old, new in zip(removed, added, strict=True)
    )


_SAFE_CLASS_CHECKERS: Dict[str, Callable[[Sequence[str], Sequence[str]], bool]] = {
    "whitespace": _is_whitespace_only_change,
    "line-wrap": _is_whitespace_only_change,
    "import-order": _is_import_order_only_change,
    "quote-style": _is_quote_style_only_change,
}

SAFE_DIFF_CLASSES: Tuple[str, ...] = tuple(_SAFE_CLASS_CHECKERS)


def _resolve_class_checkers(
    allowed_classes: Iterable[str],
) -> List[Tuple[str, Callable[[Sequence[str], Sequence[str]], bool]]]:
    """Map class names to checkers, failing loud on unknown or empty input."""
    resolved: List[Tuple[str, Callable[[Sequence[str], Sequence[str]], bool]]] = []
    for name in allowed_classes:
        checker = _SAFE_CLASS_CHECKERS.get(name)
        if checker is None:
            raise GuardExecutionError(
                f"unknown safe-diff class {name!r} (known: {', '.join(SAFE_DIFF_CLASSES)})"
            )
        resolved.append((name, checker))
    if not resolved:
        raise GuardExecutionError("allowed_classes must name at least one safe-diff class")
    return resolved


def assert_diff_is_safe_class(diff_text: str, allowed_classes: Iterable[str]) -> None:
    """Refuse a diff unless every hunk matches an allowed mechanically-safe class.

    ``allowed_classes`` draws from :data:`SAFE_DIFF_CLASSES`. Validation is
    conservative: structural changes (file adds/deletes, renames, mode
    changes, binary patches), unparseable content, and any hunk no checker
    can prove safe all raise :class:`RemediationGuardError`.
    """
    checkers = _resolve_class_checkers(allowed_classes)
    for file_change in _parse_diff_files(diff_text):
        for hunk in file_change.hunks:
            if not any(checker(hunk.removed, hunk.added) for _, checker in checkers):
                raise RemediationGuardError(
                    f"change in {file_change.header} is not provably one of the allowed safe classes "
                    f"({', '.join(name for name, _ in checkers)}); conservative reject"
                )


def _ssot_blocked_path_predicate() -> Optional[Callable[[str], bool]]:
    """Return ``check_blocked_paths.is_blocked_path`` when importable, else ``None``."""
    try:
        module = importlib.import_module("scripts.quality.check_blocked_paths")
    except ImportError:
        return None
    return getattr(module, "is_blocked_path", None)


def _fallback_is_blocked_path(path: str) -> bool:
    """Local enforcement of the SSOT blocked-paths contract (PR #241)."""
    normalized = path.replace("\\", "/").strip().removeprefix("./")
    if not normalized:
        return False
    basename = normalized.rsplit("/", 1)[-1]
    return any(
        fnmatch(normalized, pattern) or fnmatch(basename, pattern)
        for pattern in _FALLBACK_BLOCKED_PATTERNS
    )


def assert_not_blocked_path(paths: Iterable[str]) -> None:
    """Refuse candidates touching sensitive infrastructure paths.

    Delegates to ``scripts.quality.check_blocked_paths.is_blocked_path``
    when that module is importable (the SSOT, PR #241); otherwise enforces
    the same pattern contract via :data:`_FALLBACK_BLOCKED_PATTERNS`.
    """
    predicate = _ssot_blocked_path_predicate() or _fallback_is_blocked_path
    offenders = sorted({path for path in paths if predicate(path)})
    if offenders:
        raise RemediationGuardError(
            "blocked paths touched (infrastructure is off-limits to the remediation lane): "
            + ", ".join(offenders)
        )


def assert_security_class_pr_only(
    findings: Iterable[Mapping[str, Any]],
    intends_auto_merge: bool,
) -> None:
    """Delegate to ``security_class_guard.ensure_pr_only_for_security``.

    Raises :class:`RemediationGuardError` when an auto-merge is intended
    while any finding is security-class — those must always go to a PR.
    """
    try:
        security_class_guard.ensure_pr_only_for_security(
            findings, intends_auto_merge=intends_auto_merge
        )
    except security_class_guard.SecurityAutoMergeRefusedError as error:
        raise RemediationGuardError(str(error)) from error


def _worktree_untracked(repo_root: str) -> List[str]:
    """Return untracked (non-ignored) paths in the working tree."""
    return _nonempty_lines(_run_git(repo_root, ["ls-files", "--others", "--exclude-standard"]))


def _changed_paths(repo_root: str, base_ref: str, head_ref: Optional[str]) -> List[str]:
    """Return paths changed vs ``base_ref`` (plus untracked in worktree mode)."""
    diff_args = ["diff", "--no-renames", "--name-only", base_ref]
    if head_ref:
        diff_args.append(head_ref)
    changed = _nonempty_lines(_run_git(repo_root, diff_args))
    if head_ref:
        return changed
    return changed + _worktree_untracked(repo_root)


def _candidate_diff_text(repo_root: str, base_ref: str, head_ref: Optional[str]) -> str:
    """Return the full unified diff of the candidate vs ``base_ref``."""
    diff_args = ["diff", "--no-renames", base_ref]
    if head_ref:
        diff_args.append(head_ref)
    return _run_git(repo_root, diff_args)


def _assert_candidate_is_safe_class(
    repo_root: str,
    base_ref: str,
    head_ref: Optional[str],
    allowed_csv: str,
) -> None:
    """Run the safe-class diff validation for the candidate change."""
    if head_ref is None:
        untracked = _worktree_untracked(repo_root)
        if untracked:
            raise RemediationGuardError(
                "untracked files present — file creation is never formatter-only: "
                + ", ".join(sorted(untracked))
            )
    allowed = tuple(name.strip() for name in allowed_csv.split(",") if name.strip())
    assert_diff_is_safe_class(_candidate_diff_text(repo_root, base_ref, head_ref), allowed)


def _load_findings(path: str) -> List[Any]:
    """Load a findings JSON list, mapping read/parse errors to execution errors."""
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise GuardExecutionError(f"unreadable findings JSON {path}: {error}") from error
    if not isinstance(loaded, list):
        raise GuardExecutionError(
            f"findings JSON must be a list, got {type(loaded).__name__}"
        )
    return loaded


def _build_parser() -> argparse.ArgumentParser:
    """Construct the guard-runner CLI parser."""
    parser = argparse.ArgumentParser(
        description="Run all remediation-lane safety guards against a candidate change (charter §5)."
    )
    parser.add_argument("--repo-dir", default=".", help="Consumer repo working tree.")
    parser.add_argument("--base-ref", required=True, help="Pre-remediation base ref/SHA.")
    parser.add_argument(
        "--head-ref",
        default="",
        help="Candidate ref/SHA; omit to validate the working tree against --base-ref.",
    )
    parser.add_argument(
        "--safe-classes-only",
        choices=("true", "false"),
        default="true",
        help="Require the diff to be a mechanically-safe class (default: true).",
    )
    parser.add_argument(
        "--allowed-classes",
        default=",".join(SAFE_DIFF_CLASSES),
        help="Comma-separated safe-diff classes accepted when --safe-classes-only=true.",
    )
    parser.add_argument(
        "--max-deletion-ratio",
        type=float,
        default=0.5,
        help="Maximum tolerated tracked-file deletion ratio vs base (default: 0.5).",
    )
    parser.add_argument(
        "--findings-json",
        default="",
        help="Optional JSON list of findings to screen via the security-class guard.",
    )
    parser.add_argument(
        "--intends-auto-merge",
        action="store_true",
        help="Declare that the caller intends to auto-merge this candidate.",
    )
    parser.add_argument(
        "--push-arg",
        action="append",
        default=None,
        help="Intended git-push argument to screen; repeatable.",
    )
    return parser


def _run_all_guards(args: argparse.Namespace) -> int:
    """Run every guard for the parsed CLI arguments; return changed-path count."""
    head_ref = args.head_ref or None
    assert_no_force_push(args.push_arg or [])
    changed = _changed_paths(args.repo_dir, args.base_ref, head_ref)
    assert_not_blocked_path(changed)
    assert_tree_not_shrunk(
        args.repo_dir,
        args.base_ref,
        head_ref,
        max_deletion_ratio=args.max_deletion_ratio,
    )
    if args.safe_classes_only == "true":
        _assert_candidate_is_safe_class(args.repo_dir, args.base_ref, head_ref, args.allowed_classes)
    if args.findings_json:
        assert_security_class_pr_only(
            _load_findings(args.findings_json),
            intends_auto_merge=args.intends_auto_merge,
        )
    return len(changed)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint: 0 = all guards passed, 1 = guard violation, 2 = error."""
    args = _build_parser().parse_args(argv)
    try:
        changed_count = _run_all_guards(args)
    except RemediationGuardError as error:
        print(f"FAILED:remediation-guards {error}", file=sys.stderr)
        return 1
    except GuardExecutionError as error:
        print(f"ERROR:remediation-guards {error}", file=sys.stderr)
        return 2
    print(f"SUCCESS:remediation-guards {changed_count} changed path(s) passed all guards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
