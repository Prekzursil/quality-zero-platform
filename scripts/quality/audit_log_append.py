#!/usr/bin/env python3
"""Append one JSONL record to a log file on a branch, through the Contents API.

The bypass workflow used to check the platform out with a write token kept
on disk, then commit and push. Two racing runs conflicted, and the checkout
itself was flagged by CodeQL. This writes the record without any checkout:

1. read the file's CURRENT content and blob ``sha`` on the branch (a 404
   means the log does not exist yet);
2. ``PUT`` the content plus one new line, passing that ``sha``.

A concurrent writer makes the ``sha`` stale and GitHub rejects the ``PUT``
(409). The append then starts again from the new content, so no record is
ever lost or duplicated.
"""

from __future__ import absolute_import

import argparse
import base64
import json
import subprocess  # nosec B404 # noqa: S404 — gh CLI wrapper; args are controlled
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

ProcessRunner = Callable[..., "subprocess.CompletedProcess[str]"]
RETRYABLE = ("409", "422", "does not match", "wasn't supplied")


class AuditAppendError(RuntimeError):
    """The record could not be written; the audit trail must not silently drop it."""


def _read(repo: str, path: str, branch: str, *, runner: ProcessRunner) -> Tuple[str, Optional[str]]:
    """``(text, sha)`` of the file on ``branch``; ``("", None)`` when it does not exist yet."""
    done = runner(
        ["gh", "api", f"repos/{repo}/contents/{path}?ref={branch}"], capture_output=True, text=True, check=False
    )  # nosec B603 — fixed argv
    if done.returncode != 0:
        if "404" in done.stderr or "Not Found" in done.stderr:
            return "", None
        raise AuditAppendError(f"reading {path} failed: {done.stderr.strip()}")
    payload = json.loads(done.stdout)
    return base64.b64decode(payload["content"]).decode("utf-8"), str(payload["sha"])


def append_record(
    repo: str,
    path: str,
    record: str,
    *,
    branch: str,
    message: str,
    runner: ProcessRunner = subprocess.run,
    attempts: int = 3,
) -> int:
    """Append ``record`` as one line; return the attempt that succeeded. Raise if none did."""
    line = record.strip()
    json.loads(line)  # a malformed record never reaches the log
    for attempt in range(1, attempts + 1):
        text, sha = _read(repo, path, branch, runner=runner)
        if text and not text.endswith("\n"):
            text += "\n"
        body = {
            "message": message,
            "branch": branch,
            "content": base64.b64encode(f"{text}{line}\n".encode()).decode("ascii"),
        }
        if sha is not None:
            body["sha"] = sha
        done = runner(
            ["gh", "api", "-X", "PUT", f"repos/{repo}/contents/{path}", "--input", "-"],
            input=json.dumps(body),
            capture_output=True,
            text=True,
            check=False,
        )  # nosec B603 — fixed argv
        if done.returncode == 0:
            return attempt
        # 409: the file moved under us. 422 "sha wasn't supplied": another
        # writer CREATED it between our 404 read and this PUT. Both mean
        # start again from the current content.
        if not any(marker in done.stderr for marker in RETRYABLE):
            raise AuditAppendError(f"writing {path} failed: {done.stderr.strip()}")
    raise AuditAppendError(f"{path} kept changing underneath; the record was not written after {attempts} attempts")


def main(argv: Optional[Sequence[str]] = None, *, runner: ProcessRunner = subprocess.run) -> int:
    """CLI: append the LAST line of ``--record-file`` to ``--path`` on ``--branch`` of ``--repo``."""
    parser = argparse.ArgumentParser(description="Append one JSONL audit record via the Contents API.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--record-file", required=True)
    parser.add_argument("--message", required=True)
    args = parser.parse_args(argv)
    record = Path(args.record_file).read_text(encoding="utf-8").splitlines()[-1]
    try:
        attempt = append_record(args.repo, args.path, record, branch=args.branch, message=args.message, runner=runner)
    except (AuditAppendError, ValueError) as exc:
        print(f"::error::audit record not written: {exc}", file=sys.stderr)
        return 1
    print(f"audit record appended to {args.repo}:{args.branch}/{args.path} (attempt {attempt})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
