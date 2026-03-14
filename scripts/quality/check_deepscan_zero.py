#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Mapping

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report
from scripts.security_helpers import load_json_https, normalize_https_url


TOTAL_KEYS = {"total", "totalItems", "total_items", "count", "hits", "open_issues"}
DEEPSCAN_STATUS_CONTEXT = "DeepScan"
GITHUB_API_BASE = "https://api.github.com"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assert DeepScan has zero open issues.")
    parser.add_argument("--policy-mode", default="")
    parser.add_argument("--repo", default="")
    parser.add_argument("--sha", default="")
    parser.add_argument("--github-context", default=DEEPSCAN_STATUS_CONTEXT)
    parser.add_argument("--token", default="")
    parser.add_argument("--out-json", default="deepscan-zero/deepscan.json")
    parser.add_argument("--out-md", default="deepscan-zero/deepscan.md")
    return parser.parse_args()


def _nested_payload_values(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        return list(payload.values())
    if isinstance(payload, list):
        return list(payload)
    return []


def extract_total_open(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in TOTAL_KEYS and isinstance(value, (int, float)):
                return int(value)
    for nested in _nested_payload_values(payload):
        total = extract_total_open(nested)
        if total is not None:
            return total
    return None


def _request_json(url: str, token: str) -> dict[str, Any]:
    payload, _ = load_json_https(
        url,
        allowed_host_suffixes={"deepscan.io"},
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "quality-zero-platform",
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected DeepScan API response payload")
    return payload


def _github_status_payload(repo: str, sha: str, token: str) -> dict[str, Any]:
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


def _render_md(payload: Mapping[str, Any]) -> str:
    lines = [
        "# DeepScan Zero Gate",
        "",
        f"- Status: `{payload['status']}`",
        f"- Open issues: `{payload.get('open_issues')}`",
        f"- Source URL: `{payload.get('open_issues_url') or 'n/a'}`",
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
        "",
        "## Findings",
    ]
    lines.extend([f"- {item}" for item in payload.get("findings", [])] or ["- None"])
    return "\n".join(lines) + "\n"


def _policy_mode(args: argparse.Namespace) -> str:
    return (args.policy_mode or os.environ.get("DEEPSCAN_POLICY_MODE", "")).strip() or "open_issues_url"


def _github_repo(args: argparse.Namespace) -> str:
    return (args.repo or os.environ.get("REPO_SLUG", "") or os.environ.get("GITHUB_REPOSITORY", "")).strip()


def _github_sha(args: argparse.Namespace) -> str:
    return (args.sha or os.environ.get("TARGET_SHA", "") or os.environ.get("GITHUB_SHA", "")).strip()


def _validate_github_check_context_inputs(github_token: str, repo: str, sha: str) -> list[str]:
    findings: list[str] = []
    if not github_token:
        findings.append("GITHUB_TOKEN is missing for github_check_context mode.")
    if not repo:
        findings.append("REPO_SLUG or GITHUB_REPOSITORY is missing for github_check_context mode.")
    if not sha:
        findings.append("TARGET_SHA or GITHUB_SHA is missing for github_check_context mode.")
    return findings


def _validate_open_issues_mode_inputs(token: str, open_issues_url: str) -> list[str]:
    findings: list[str] = []
    if not token:
        findings.append("DEEPSCAN_API_TOKEN is missing.")
    if not open_issues_url:
        findings.append("DEEPSCAN_OPEN_ISSUES_URL is missing.")
    return findings


def _validate_deepscan_inputs(
    *,
    token: str,
    policy_mode: str,
    open_issues_url: str,
    github_token: str,
    repo: str,
    sha: str,
) -> list[str]:
    if policy_mode == "github_check_context":
        return _validate_github_check_context_inputs(github_token, repo, sha)
    return _validate_open_issues_mode_inputs(token, open_issues_url)


def _normalized_open_issues_url(open_issues_url: str) -> str:
    return normalize_https_url(open_issues_url, allowed_host_suffixes={"deepscan.io"})


def _evaluate_deepscan_open_issues(open_issues_url: str, token: str) -> tuple[int | None, list[str]]:
    findings: list[str] = []
    payload = _request_json(open_issues_url, token)
    open_issues = extract_total_open(payload)
    if open_issues is None:
        findings.append("DeepScan response did not include a parseable total issue count.")
    elif open_issues != 0:
        findings.append(f"DeepScan reports {open_issues} open issues (expected 0).")
    return open_issues, findings


def _find_github_status(payload: dict[str, Any], context: str) -> dict[str, Any] | None:
    for item in payload.get("statuses", []) or []:
        if str(item.get("context") or "").strip() == context:
            return item
    return None


def _status_target_url(status: dict[str, Any] | None) -> str:
    return str((status or {}).get("target_url") or "").strip()


def _status_findings(status: dict[str, Any] | None, context: str) -> list[str]:
    if status is None:
        return [f"{context} GitHub status context is missing."]

    state = str(status.get("state") or "").strip()
    if state == "success":
        return []
    return [f"{context} GitHub status is {state or 'unknown'} (expected success)."]


def _evaluate_github_check_context(args: argparse.Namespace, token: str) -> tuple[int | None, str, list[str]]:
    payload = _github_status_payload(_github_repo(args), _github_sha(args), token)
    context = str(getattr(args, "github_context", DEEPSCAN_STATUS_CONTEXT) or DEEPSCAN_STATUS_CONTEXT)
    status = _find_github_status(payload, context)
    findings = _status_findings(status, context)
    open_issues = 0 if not findings else None
    return open_issues, _status_target_url(status), findings


def _evaluate_open_issues_mode(open_issues_url: str, token: str) -> tuple[int | None, str, list[str]]:
    normalized_url = _normalized_open_issues_url(open_issues_url)
    open_issues, findings = _evaluate_deepscan_open_issues(normalized_url, token)
    return open_issues, normalized_url, findings


def _evaluate_deepscan_policy(
    args: argparse.Namespace,
    *,
    policy_mode: str,
    token: str,
    github_token: str,
    open_issues_url: str,
) -> tuple[int | None, str, list[str]]:
    if policy_mode == "github_check_context":
        return _evaluate_github_check_context(args, github_token)
    return _evaluate_open_issues_mode(open_issues_url, token)


def main() -> int:
    args = _parse_args()
    token = (args.token or os.environ.get("DEEPSCAN_API_TOKEN", "")).strip()
    github_token = os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip()
    policy_mode = _policy_mode(args)
    open_issues_url = os.environ.get("DEEPSCAN_OPEN_ISSUES_URL", "").strip()

    findings = _validate_deepscan_inputs(
        token=token,
        policy_mode=policy_mode,
        open_issues_url=open_issues_url,
        github_token=github_token,
        repo=_github_repo(args),
        sha=_github_sha(args),
    )
    open_issues: int | None = None
    source_url = open_issues_url

    status = "fail"
    if not findings:
        try:
            open_issues, source_url, findings = _evaluate_deepscan_policy(
                args,
                policy_mode=policy_mode,
                token=token,
                github_token=github_token,
                open_issues_url=open_issues_url,
            )
            status = "pass" if not findings else "fail"
        except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
            findings.append(f"DeepScan API request failed: {exc}")

    payload = {
        "status": status,
        "open_issues": open_issues,
        "open_issues_url": source_url,
        "timestamp_utc": utc_timestamp(),
        "findings": findings,
    }
    return_code = write_report(
        payload,
        out_json=args.out_json,
        out_md=args.out_md,
        default_json="deepscan-zero/deepscan.json",
        default_md="deepscan-zero/deepscan.md",
        render_md=_render_md,
    )
    if return_code != 0:
        return return_code
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
