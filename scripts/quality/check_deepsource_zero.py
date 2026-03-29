#!/usr/bin/env python3
"""Assert that DeepSource shows zero visible issues for the default branch."""

from __future__ import absolute_import

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report
from scripts.quality.deepsource_html import (
    extract_issue_links,
    extract_visible_issue_count,
    human_count_to_int as _human_count_to_int,
)
from scripts.security_helpers import (
    load_bytes_https,
    load_json_https,
    normalize_https_url,
)


DEEPSOURCE_STATUS_PREFIX = "DeepSource"
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_POLL_SECONDS = 20
GITHUB_API_BASE = "https://api.github.com"


@dataclass(frozen=True)
class StatusPollRequest:
    """Describe the GitHub status contexts required for a DeepSource check."""

    repo: str
    sha: str
    token: str
    prefix: str
    timeout_seconds: int
    poll_seconds: int


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the DeepSource visible-zero gate."""
    parser = argparse.ArgumentParser(
        description="Assert DeepSource has zero visible default-branch issues."
    )
    parser.add_argument("--repo", default="")
    parser.add_argument("--sha", default="")
    parser.add_argument("--issues-url", default="")
    parser.add_argument("--status-prefix", default=DEEPSOURCE_STATUS_PREFIX)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--out-json", default="deepsource-visible-zero/deepsource.json")
    parser.add_argument("--out-md", default="deepsource-visible-zero/deepsource.md")
    return parser.parse_args()


def _github_repo(args: argparse.Namespace) -> str:
    """Resolve the repository slug from flags or the GitHub Actions env."""
    return (
        args.repo
        or os.environ.get("REPO_SLUG", "")
        or os.environ.get("GITHUB_REPOSITORY", "")
    ).strip()


def _github_sha(args: argparse.Namespace) -> str:
    """Resolve the target commit SHA from flags or the GitHub Actions env."""
    return (
        args.sha or os.environ.get("TARGET_SHA", "") or os.environ.get("GITHUB_SHA", "")
    ).strip()


def _issues_url(args: argparse.Namespace) -> str:
    """Resolve the default-branch DeepSource issues URL for the repo."""
    explicit = (args.issues_url or os.environ.get("DEEPSOURCE_ISSUES_URL", "")).strip()
    if explicit:
        return normalize_https_url(explicit, allowed_hosts={"app.deepsource.com"})
    repo = _github_repo(args)
    if not repo:
        return ""
    return normalize_https_url(
        f"https://app.deepsource.com/gh/{repo}/issues?category=all&page=1",
        allowed_hosts={"app.deepsource.com"},
    )


def _github_status_payload(repo: str, sha: str, token: str) -> Dict[str, Any]:
    """Fetch the GitHub status payload for a commit."""
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


def _request_html(url: str) -> str:
    """Fetch a DeepSource HTML page as UTF-8 text."""
    payload, _ = load_bytes_https(
        url,
        allowed_hosts={"app.deepsource.com"},
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "quality-zero-platform",
        },
    )
    return payload.decode("utf-8", "ignore")


def _status_contexts(payload: Mapping[str, Any], prefix: str) -> List[Dict[str, Any]]:
    """Collect GitHub commit statuses whose contexts belong to DeepSource."""
    normalized_prefix = prefix.strip().lower()
    return [
        item
        for item in payload.get("statuses", []) or []
        if isinstance(item, dict)
        and (context := str(item.get("context") or "").strip())
        and context.lower().startswith(normalized_prefix)
    ]


def _status_target_urls(statuses: Sequence[Mapping[str, Any]]) -> List[str]:
    """Collect unique DeepSource target URLs from GitHub statuses."""
    return list(
        dict.fromkeys(
            target_url
            for item in statuses
            if (target_url := str(item.get("target_url") or "").strip())
        )
    )


def _status_finding(item: Mapping[str, Any], prefix: str) -> str | None:
    """Convert one DeepSource GitHub status context into a gate finding."""
    context = str(item.get("context") or prefix).strip()
    state = str(item.get("state") or "").strip()
    if state == "success":
        return None
    qualifier = "pending" if state == "pending" else state or "unknown"
    return f"{context} GitHub status is {qualifier} (expected success)."


def _status_findings(statuses: Sequence[Mapping[str, Any]], prefix: str) -> List[str]:
    """Return gate findings for missing or failing DeepSource statuses."""
    if not statuses:
        return [f"{prefix} GitHub status contexts are missing."]
    return [
        finding
        for item in statuses
        if (finding := _status_finding(item, prefix)) is not None
    ]


def _statuses_are_ready(statuses: Sequence[Mapping[str, Any]]) -> bool:
    """Return ``True`` when all observed statuses have settled."""
    return bool(statuses) and all(
        str(item.get("state") or "").strip() != "pending"
        for item in statuses
    )


def _wait_for_status_contexts(
    request: StatusPollRequest,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Poll GitHub statuses until DeepSource contexts settle or time runs out."""
    deadline = time.time() + max(request.timeout_seconds, 1)
    statuses: List[Dict[str, Any]] = []
    while time.time() <= deadline:
        payload = _github_status_payload(request.repo, request.sha, request.token)
        statuses = _status_contexts(payload, request.prefix)
        if _statuses_are_ready(statuses):
            break
        time.sleep(max(request.poll_seconds, 0))
    return statuses, _status_findings(statuses, request.prefix)


def _evaluate_visible_issues(issues_url: str) -> Tuple[int, List[str]]:
    """Evaluate the public DeepSource issues page for visible backlog."""
    html = _request_html(issues_url)
    open_issues = extract_visible_issue_count(html)
    issue_links = extract_issue_links(html)
    if open_issues is None:
        open_issues = 0 if not issue_links else len(issue_links)
    findings: List[str] = []
    if open_issues != 0:
        findings.append(
            f"DeepSource shows {open_issues} visible issues on the default branch "
            f"(expected 0)."
        )
    elif issue_links:
        findings.append(
            "DeepSource returned issue cards even though the total issue count "
            "resolved to 0."
        )
    return open_issues, findings


def _validate_inputs(repo: str, sha: str, issues_url: str, token: str) -> List[str]:
    """Return input validation findings for the current execution context."""
    return [
        message
        for message, value in (
            ("GITHUB_TOKEN or GH_TOKEN is required.", token),
            ("REPO_SLUG or GITHUB_REPOSITORY is required.", repo),
            ("TARGET_SHA or GITHUB_SHA is required.", sha),
            ("DeepSource issues URL could not be resolved.", issues_url),
        )
        if not value
    ]


def _render_md(payload: Mapping[str, Any]) -> str:
    """Render a markdown summary for the DeepSource visible-zero lane."""
    lines = [
        "# DeepSource Visible Zero Gate",
        "",
        f"- Status: `{payload['status']}`",
        f"- Visible issues: `{payload.get('open_issues')}`",
        f"- Issues URL: `{payload.get('issues_url') or 'n/a'}`",
        (
            f"- DeepSource statuses: "
            f"`{', '.join(payload.get('status_contexts', [])) or 'n/a'}`"
        ),
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
        "",
        "## Findings",
    ]
    lines.extend([f"- {item}" for item in payload.get("findings", [])] or ["- None"])
    return "\n".join(lines) + "\n"


def main() -> int:
    """Run the DeepSource visible-zero gate."""
    args = _parse_args()
    token = (
        os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")
    ).strip()
    repo = _github_repo(args)
    sha = _github_sha(args)
    issues_url = _issues_url(args)

    findings = _validate_inputs(repo, sha, issues_url, token)
    statuses: List[Dict[str, Any]] = []
    open_issues = 0
    status = "fail"
    if not findings:
        try:
            statuses, findings = _wait_for_status_contexts(
                StatusPollRequest(
                    repo=repo,
                    sha=sha,
                    token=token,
                    prefix=args.status_prefix,
                    timeout_seconds=args.timeout_seconds,
                    poll_seconds=args.poll_seconds,
                )
            )
            if not findings:
                open_issues, findings = _evaluate_visible_issues(issues_url)
            status = "pass" if not findings else "fail"
        except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
            findings.append(f"DeepSource request failed: {exc}")

    payload = {
        "status": status,
        "open_issues": open_issues,
        "issues_url": issues_url,
        "status_contexts": [
            str(item.get("context") or "").strip()
            for item in statuses
            if item.get("context")
        ],
        "target_urls": _status_target_urls(statuses),
        "timestamp_utc": utc_timestamp(),
        "findings": findings,
    }
    return_code = write_report(
        payload,
        out_json=args.out_json,
        out_md=args.out_md,
        default_json="deepsource-visible-zero/deepsource.json",
        default_md="deepsource-visible-zero/deepsource.md",
        render_md=_render_md,
    )
    if return_code != 0:
        return return_code
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
