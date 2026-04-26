#!/usr/bin/env python3
"""Apply drift-sync fixes + open a PR on the consumer repo.

Called by ``reusable-drift-sync.yml`` after ``drift_sync.py`` has
emitted the drift report JSON. Writes the ``proposed_content`` for
every ``missing`` / ``drift`` entry to its ``output_path``, commits
to a fresh branch, and opens a pull request via ``gh pr create``.

Intentionally does not manipulate the consumer repo when the report
shows ``in_sync`` for every entry — that path yields exit 0 with no
side effects so the workflow can always call this step.
"""

from __future__ import absolute_import

import argparse
import json
import os
import subprocess  # nosec B404 # noqa: S404 — gh CLI wrapper; args are controlled
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping

_BRANCH_PREFIX = "quality-zero/drift-sync"


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--repo-slug", required=True)
    parser.add_argument("--default-branch", default="main")
    parser.add_argument("--platform-ref", default="main")
    parser.add_argument(
        "--cwd", default=".",
        help="Working directory (consumer repo checkout). Defaults to current dir.",
    )
    parser.add_argument(
        "--runner",
        default=None,
        help=argparse.SUPPRESS,  # test-only injection of a subprocess.run stub
    )
    return parser.parse_args()


def _collect_out_of_sync(entries: List[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """Return only entries whose status is ``missing`` or ``drift``."""
    return [e for e in entries if e.get("status") in ("missing", "drift")]


def _apply_entries(entries: List[Mapping[str, Any]], cwd: Path) -> List[str]:
    """Write each entry's proposed content to disk; return staged paths."""
    applied: List[str] = []
    for entry in entries:
        rel = str(entry.get("output_path", "")).strip()
        body = str(entry.get("proposed_content", ""))
        if not rel:
            continue
        target = cwd / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        applied.append(rel)
    return applied


def _git(
    args: List[str],
    cwd: Path,
    runner,
) -> subprocess.CompletedProcess[str]:
    """Run a git command, streaming output through ``runner``."""
    return runner(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _gh_pr_create(
    branch: str,
    default_branch: str,
    summary_body: str,
    cwd: Path,
    runner,
) -> None:
    """Invoke ``gh pr create`` with the staged branch + enable auto-merge.

    Phase 3 contract: drift-sync PRs auto-merge on green CI.
    ``gh pr create`` itself has no auto-merge flag, so a follow-up
    ``gh pr merge --auto --squash`` arms the merge once CI clears.
    A failure to enable auto-merge is logged but **not fatal** — the
    PR still exists and the operator can merge it manually. This
    asymmetric tolerance prevents drift PRs from being orphaned by a
    transient ``gh`` hiccup on the auto-merge step.
    """
    runner(
        [
            "gh", "pr", "create",
            "--base", default_branch,
            "--head", branch,
            "--title", "chore(drift-sync): apply template updates from quality-zero-platform",
            "--body", summary_body,
        ],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    # gh resolves the PR by current branch — no need to parse the URL.
    try:
        runner(
            ["gh", "pr", "merge", branch, "--auto", "--squash"],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        # Auto-merge is best-effort. Common failure modes: repo-level
        # auto-merge disabled, branch protection prevents squash, token
        # lacks permission. Log and continue so the PR is still useful.
        print(
            f"apply_drift_pr: enabling auto-merge on {branch} failed "
            f"(exit {exc.returncode}); PR remains open for manual merge. "
            f"stderr: {exc.stderr}",
            file=sys.stderr,
            flush=True,
        )


def _build_body(
    out_of_sync: List[Mapping[str, Any]], platform_ref: str
) -> str:
    """Build the PR body summary from the drift entries."""
    lines = [
        "Automated template-drift sync from quality-zero-platform.",
        "",
        f"Platform ref: `{platform_ref}`",
        "",
        "Updated files:",
    ]
    for entry in out_of_sync:
        status = entry.get("status", "")
        path = entry.get("output_path", "")
        lines.append(f"- `{path}` ({status})")
    return "\n".join(lines) + "\n"


def _load_report(report_path: Path) -> Dict[str, Any] | None:
    """Return the drift report JSON or ``None`` when missing."""
    if not report_path.is_file():
        print(
            f"apply_drift_pr: drift report not found: {report_path}",
            file=sys.stderr, flush=True,
        )
        return None
    return json.loads(report_path.read_text(encoding="utf-8"))


def _git_commit_and_push(
    branch: str, applied: List[str], cwd: Path, runner,
) -> None:
    """Run the git checkout/add/commit/push sequence."""
    _git(["checkout", "-b", branch], cwd, runner)
    _git(["add", *applied], cwd, runner)
    _git(
        [
            "-c", "user.email=platform-bot@quality-zero.local",
            "-c", "user.name=quality-zero-platform drift-sync",
            "commit",
            "-m", "chore(drift-sync): apply template updates",
        ],
        cwd, runner,
    )
    _git(["push", "--set-upstream", "origin", branch], cwd, runner)


def _run_drift_pr(args: argparse.Namespace, runner) -> int:
    """Body of :func:`main`, split out so tests can inject ``runner``."""
    payload = _load_report(Path(args.report))
    if payload is None:
        return 2
    out_of_sync = _collect_out_of_sync(list(payload.get("entries") or []))
    if not out_of_sync:
        print("apply_drift_pr: fleet already in sync; nothing to do.", flush=True)
        return 0
    cwd = Path(args.cwd).resolve()
    branch = f"{_BRANCH_PREFIX}/{os.environ.get('GITHUB_RUN_ID', 'manual')}"
    applied = _apply_entries(out_of_sync, cwd)
    if not applied:
        print("apply_drift_pr: no entries had output paths to apply.", flush=True)
        return 0
    _git_commit_and_push(branch, applied, cwd, runner)
    _gh_pr_create(
        branch, args.default_branch, _build_body(out_of_sync, args.platform_ref),
        cwd, runner,
    )
    print(
        f"apply_drift_pr: opened PR on {args.repo_slug} "
        f"with {len(applied)} file(s) updated.",
        flush=True,
    )
    return 0


def main() -> int:
    """CLI entrypoint."""
    args = _parse_args()
    return _run_drift_pr(args, subprocess.run)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
