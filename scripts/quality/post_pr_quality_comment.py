#!/usr/bin/env python3
"""Post pr quality comment."""

from __future__ import absolute_import

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict
from urllib.error import HTTPError

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.security_helpers import load_json_https

MARKER = "<!-- quality-zero-rollup -->"
GITHUB_API_BASE = "https://api.github.com"


def parse_args() -> argparse.Namespace:
    """Handle parse args."""
    parser = argparse.ArgumentParser(description="Create or update the sticky quality rollup PR comment.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pull-request", required=True)
    parser.add_argument("--markdown-file", required=True)
    return parser.parse_args()


def render_comment_body(markdown: str) -> str:
    """Handle render comment body."""
    return f"{MARKER}\n\n{markdown.strip()}\n"


def _github_request(url: str, token: str, *, method: str = "GET", data: Dict[str, Any] | None = None) -> Any:
    """Handle github request."""
    payload, _ = load_json_https(
        url,
        allowed_hosts={"api.github.com"},
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "quality-zero-platform",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
        method=method,
        data=(json.dumps(data).encode("utf-8") if data is not None else None),
    )
    return payload


def upsert_comment(*, repo: str, pull_request: str, body: str, token: str) -> int:
    """Handle upsert comment."""
    issue_comments_url = f"{GITHUB_API_BASE}/repos/{repo}/issues/{pull_request}/comments"
    comments = _github_request(issue_comments_url, token)
    if isinstance(comments, list):
        for comment in comments:
            if MARKER in str(comment.get("body") or ""):
                comment_id = int(comment["id"])
                _github_request(
                    f"{GITHUB_API_BASE}/repos/{repo}/issues/comments/{comment_id}",
                    token,
                    method="PATCH",
                    data={"body": body},
                )
                return comment_id

    created = _github_request(issue_comments_url, token, method="POST", data={"body": body})
    return int(created["id"])


def main() -> int:
    """Handle main."""
    args = parse_args()
    token = (os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")).strip()
    if not token:
        raise SystemExit("GITHUB_TOKEN or GH_TOKEN is required")
    markdown = Path(args.markdown_file).read_text(encoding="utf-8")
    try:
        upsert_comment(repo=args.repo, pull_request=args.pull_request, body=render_comment_body(markdown), token=token)
    except (HTTPError, OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Unable to post PR comment: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
