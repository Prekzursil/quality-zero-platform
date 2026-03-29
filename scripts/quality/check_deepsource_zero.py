#!/usr/bin/env python3
from __future__ import absolute_import

import argparse
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report
from scripts.security_helpers import load_bytes_https, load_json_https, normalize_https_url


DEEPSOURCE_STATUS_PREFIX = "DeepSource"
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_POLL_SECONDS = 20
GITHUB_API_BASE = "https://api.github.com"
ALL_ISSUES_PATTERNS = (
    re.compile(r"All issues\s*</span>\s*<div[^>]*>([^<]+)</div>", re.IGNORECASE | re.DOTALL),
    re.compile(r'"all",\s*(\d+)\s*,\s*"recommended"', re.IGNORECASE),
)
ISSUE_LINK_PATTERN = re.compile(r'href="(/gh/[^"]+/issue/[^"]+/occurrences\?listindex=0)"')


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assert DeepSource has zero visible default-branch issues.")
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
    return (args.repo or os.environ.get("REPO_SLUG", "") or os.environ.get("GITHUB_REPOSITORY", "")).strip()


def _github_sha(args: argparse.Namespace) -> str:
    return (args.sha or os.environ.get("TARGET_SHA", "") or os.environ.get("GITHUB_SHA", "")).strip()


def _issues_url(args: argparse.Namespace) -> str:
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
    payload, _ = load_bytes_https(
        url,
        allowed_hosts={"app.deepsource.com"},
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "quality-zero-platform",
        },
    )
    return payload.decode("utf-8", "ignore")


def _human_count_to_int(raw_value: str) -> int | None:
    value = raw_value.strip().lower().replace(",", "")
    if not value:
        return None
    multiplier = 1
    if value.endswith("k"):
        multiplier = 1000
        value = value[:-1]
    try:
        return int(math.ceil(float(value) * multiplier))
    except ValueError:
        return None


def extract_visible_issue_count(html: str) -> int | None:
    for pattern in ALL_ISSUES_PATTERNS:
        match = pattern.search(html)
        if not match:
            continue
        parsed = _human_count_to_int(match.group(1))
        if parsed is not None:
            return parsed
    return None


def extract_issue_links(html: str) -> List[str]:
    return sorted(set(ISSUE_LINK_PATTERN.findall(html)))


def _status_contexts(payload: Mapping[str, Any], prefix: str) -> List[Dict[str, Any]]:
    contexts: List[Dict[str, Any]] = []
    normalized_prefix = prefix.strip().lower()
    for item in payload.get("statuses", []) or []:
        if not isinstance(item, dict):
            continue
        context = str(item.get("context") or "").strip()
        if not context:
            continue
        if context.lower().startswith(normalized_prefix):
            contexts.append(item)
    return contexts


def _status_target_urls(statuses: Sequence[Mapping[str, Any]]) -> List[str]:
    urls: List[str] = []
    for item in statuses:
        target_url = str(item.get("target_url") or "").strip()
        if target_url and target_url not in urls:
            urls.append(target_url)
    return urls


def _status_findings(statuses: Sequence[Mapping[str, Any]], prefix: str) -> List[str]:
    if not statuses:
        return [f"{prefix} GitHub status contexts are missing."]
    findings: List[str] = []
    for item in statuses:
        context = str(item.get("context") or prefix).strip()
        state = str(item.get("state") or "").strip()
        if state == "success":
            continue
        if state == "pending":
            findings.append(f"{context} GitHub status is pending (expected success).")
        else:
            findings.append(f"{context} GitHub status is {state or 'unknown'} (expected success).")
    return findings


def _wait_for_status_contexts(
    *,
    repo: str,
    sha: str,
    token: str,
    prefix: str,
    timeout_seconds: int,
    poll_seconds: int,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    deadline = time.time() + max(timeout_seconds, 1)
    statuses: List[Dict[str, Any]] = []
    while time.time() <= deadline:
        payload = _github_status_payload(repo, sha, token)
        statuses = _status_contexts(payload, prefix)
        if statuses and all(str(item.get("state") or "").strip() != "pending" for item in statuses):
            break
        time.sleep(max(poll_seconds, 0))
    return statuses, _status_findings(statuses, prefix)


def _evaluate_visible_issues(issues_url: str) -> Tuple[int, List[str]]:
    html = _request_html(issues_url)
    open_issues = extract_visible_issue_count(html)
    issue_links = extract_issue_links(html)
    if open_issues is None:
        open_issues = 0 if not issue_links else len(issue_links)
    findings: List[str] = []
    if open_issues != 0:
        findings.append(f"DeepSource shows {open_issues} visible issues on the default branch (expected 0).")
    elif issue_links:
        findings.append("DeepSource returned issue cards even though the total issue count resolved to 0.")
    return open_issues, findings


def _validate_inputs(repo: str, sha: str, issues_url: str, token: str) -> List[str]:
    findings: List[str] = []
    if not token:
        findings.append("GITHUB_TOKEN or GH_TOKEN is required.")
    if not repo:
        findings.append("REPO_SLUG or GITHUB_REPOSITORY is required.")
    if not sha:
        findings.append("TARGET_SHA or GITHUB_SHA is required.")
    if not issues_url:
        findings.append("DeepSource issues URL could not be resolved.")
    return findings


def _render_md(payload: Mapping[str, Any]) -> str:
    lines = [
        "# DeepSource Visible Zero Gate",
        "",
        f"- Status: `{payload['status']}`",
        f"- Visible issues: `{payload.get('open_issues')}`",
        f"- Issues URL: `{payload.get('issues_url') or 'n/a'}`",
        f"- DeepSource statuses: `{', '.join(payload.get('status_contexts', [])) or 'n/a'}`",
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
        "",
        "## Findings",
    ]
    lines.extend([f"- {item}" for item in payload.get("findings", [])] or ["- None"])
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _parse_args()
    token = (os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")).strip()
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
                repo=repo,
                sha=sha,
                token=token,
                prefix=args.status_prefix,
                timeout_seconds=args.timeout_seconds,
                poll_seconds=args.poll_seconds,
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
        "status_contexts": [str(item.get("context") or "").strip() for item in statuses if item.get("context")],
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
