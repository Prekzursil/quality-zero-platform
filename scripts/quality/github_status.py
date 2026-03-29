"""GitHub commit status helpers shared by quality gates."""

from __future__ import absolute_import

from typing import Any, Dict

from scripts.security_helpers import load_json_https


GITHUB_API_BASE = "https://api.github.com"


def load_commit_status_payload(repo: str, sha: str, token: str) -> Dict[str, Any]:
    """Fetch the GitHub commit status payload for a repository SHA."""
    payload, _ = load_json_https(
        f"{GITHUB_API_BASE}/repos/{repo}/commits/{sha}/status",
        allowed_hosts={"api.github.com"},
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "quality-zero-platform",
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected GitHub status response payload")
    return payload
