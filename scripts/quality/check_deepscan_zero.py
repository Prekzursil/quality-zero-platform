#!/usr/bin/env python3
"""Check DeepScan for zero open issues."""

from __future__ import absolute_import

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report
from scripts.security_helpers import load_json_https, normalize_https_url


TOTAL_KEYS = {"total", "totalItems", "total_items", "count", "hits", "open_issues"}
DEEPSCAN_STATUS_CONTEXT = "DeepScan"
GITHUB_API_BASE = "https://api.github.com"


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the DeepScan gate."""
    parser = argparse.ArgumentParser(
        description="Assert DeepScan has zero open issues.",
    )
    parser.add_argument("--policy-mode", default="")
    parser.add_argument("--repo", default="")
    parser.add_argument("--sha", default="")
    parser.add_argument("--github-context", default=DEEPSCAN_STATUS_CONTEXT)
    parser.add_argument("--token", default="")
    parser.add_argument("--out-json", default="deepscan-zero/deepscan.json")
    parser.add_argument("--out-md", default="deepscan-zero/deepscan.md")
    return parser.parse_args()


def _event_name() -> str:
    """Return the current GitHub event name, if any."""
    return os.environ.get("EVENT_NAME", "").strip()


def _nested_payload_values(payload: Any) -> List[Any]:
    """Return nested payload values for recursive issue-count discovery."""
    if isinstance(payload, dict):
        return list(payload.values())
    if isinstance(payload, list):
        return list(payload)
    return []


def extract_total_open(payload: Any) -> int | None:
    """Extract the total open issue count from a nested DeepScan payload."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in TOTAL_KEYS and isinstance(value, (int, float)):
                return int(value)
    for nested in _nested_payload_values(payload):
        total = extract_total_open(nested)
        if total is not None:
            return total
    return None


def _request_json(url: str, token: str) -> Dict[str, Any]:
    """Fetch a JSON payload from the DeepScan API."""
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


def _github_status_payload(repo: str, sha: str, token: str) -> Dict[str, Any]:
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


def _render_md(payload: Mapping[str, Any]) -> str:
    """Render the DeepScan result payload as markdown."""
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
    """Resolve the DeepScan policy mode from CLI args or environment."""
    return (
        (args.policy_mode or os.environ.get("DEEPSCAN_POLICY_MODE", "")).strip()
        or "github_check_context"
    )


def _github_repo(args: argparse.Namespace) -> str:
    """Resolve the GitHub repository slug from CLI args or environment."""
    return (
        args.repo
        or os.environ.get("REPO_SLUG", "")
        or os.environ.get("GITHUB_REPOSITORY", "")
    ).strip()


def _github_sha(args: argparse.Namespace) -> str:
    """Resolve the target commit SHA from CLI args or environment."""
    return (
        args.sha
        or os.environ.get("TARGET_SHA", "")
        or os.environ.get("GITHUB_SHA", "")
    ).strip()


def _validate_github_check_context_inputs(
    github_token: str,
    repo: str,
    sha: str,
) -> List[str]:
    """Validate inputs required for GitHub status-context mode."""
    findings: List[str] = []
    if not github_token:
        findings.append("GITHUB_TOKEN is missing for github_check_context mode.")
    if not repo:
        findings.append(
            "REPO_SLUG or GITHUB_REPOSITORY is missing for github_check_context mode."
        )
    if not sha:
        findings.append(
            "TARGET_SHA or GITHUB_SHA is missing for github_check_context mode."
        )
    return findings


def _validate_open_issues_mode_inputs(token: str, open_issues_url: str) -> List[str]:
    """Validate inputs required for direct DeepScan open-issues mode."""
    findings: List[str] = []
    if not token:
        findings.append("DEEPSCAN_API_TOKEN is missing.")
    if not open_issues_url:
        findings.append("DEEPSCAN_OPEN_ISSUES_URL is missing.")
    return findings


def _validate_deepscan_inputs(*args: Any, **kwargs: Any) -> List[str]:
    """Validate DeepScan inputs for the selected policy mode."""
    if args:
        raise TypeError("_validate_deepscan_inputs expects keyword arguments only")
    try:
        token = str(kwargs.pop("token"))
        policy_mode = str(kwargs.pop("policy_mode"))
        open_issues_url = str(kwargs.pop("open_issues_url"))
        github_token = str(kwargs.pop("github_token"))
        repo = str(kwargs.pop("repo"))
        sha = str(kwargs.pop("sha"))
    except KeyError as exc:  # pragma: no cover - defensive contract guard
        raise TypeError(f"Missing required DeepScan parameter: {exc.args[0]}") from exc
    if kwargs:
        raise TypeError(
            "Unexpected _validate_deepscan_inputs parameters: "
            f"{', '.join(sorted(kwargs))}"
        )
    if policy_mode == "github_check_context":
        return _validate_github_check_context_inputs(github_token, repo, sha)
    return _validate_open_issues_mode_inputs(token, open_issues_url)


def _normalized_open_issues_url(open_issues_url: str) -> str:
    """Normalize and validate the DeepScan open-issues URL."""
    return normalize_https_url(open_issues_url, allowed_host_suffixes={"deepscan.io"})


def _evaluate_deepscan_open_issues(
    open_issues_url: str,
    token: str,
) -> Tuple[int | None, List[str]]:
    """Evaluate a DeepScan open-issues endpoint and collect findings."""
    findings: List[str] = []
    payload = _request_json(open_issues_url, token)
    open_issues = extract_total_open(payload)
    if open_issues is None:
        findings.append(
            "DeepScan response did not include a parseable total issue count."
        )
    elif open_issues != 0:
        findings.append(f"DeepScan reports {open_issues} open issues (expected 0).")
    return open_issues, findings


def _find_github_status(payload: Dict[str, Any], context: str) -> Dict[str, Any] | None:
    """Return the matching GitHub commit status for the requested context."""
    for item in payload.get("statuses", []) or []:
        if str(item.get("context") or "").strip() == context:
            return item
    return None


def _status_target_url(status: Dict[str, Any] | None) -> str:
    """Extract the target URL from a GitHub commit status payload."""
    return str((status or {}).get("target_url") or "").strip()


def _status_findings(status: Dict[str, Any] | None, context: str) -> List[str]:
    """Translate a GitHub commit status into gate findings."""
    if status is None:
        return [f"{context} GitHub status context is missing."]

    state = str(status.get("state") or "").strip()
    if state == "success":
        return []
    return [f"{context} GitHub status is {state or 'unknown'} (expected success)."]


def _evaluate_github_check_context(
    args: argparse.Namespace,
    token: str,
) -> Tuple[int | None, str, List[str]]:
    """Evaluate DeepScan via the GitHub commit-status context."""
    payload = _github_status_payload(_github_repo(args), _github_sha(args), token)
    github_context = getattr(args, "github_context", DEEPSCAN_STATUS_CONTEXT)
    context = str(github_context or DEEPSCAN_STATUS_CONTEXT)
    status = _find_github_status(payload, context)
    if status is None and _event_name() in {"push", "workflow_dispatch"}:
        return 0, "", []
    findings = _status_findings(status, context)
    open_issues = 0 if not findings else None
    return open_issues, _status_target_url(status), findings


def _evaluate_open_issues_mode(
    open_issues_url: str,
    token: str,
) -> Tuple[int | None, str, List[str]]:
    """Evaluate DeepScan via a direct open-issues API URL."""
    normalized_url = _normalized_open_issues_url(open_issues_url)
    open_issues, findings = _evaluate_deepscan_open_issues(normalized_url, token)
    return open_issues, normalized_url, findings


def _evaluate_deepscan_policy(
    args: argparse.Namespace,
    *call_args: Any,
    **kwargs: Any,
) -> Tuple[int | None, str, List[str]]:
    """Dispatch DeepScan evaluation according to the selected policy mode."""
    if call_args:
        raise TypeError("_evaluate_deepscan_policy expects keyword arguments only")
    try:
        policy_mode = str(kwargs.pop("policy_mode"))
        token = str(kwargs.pop("token"))
        github_token = str(kwargs.pop("github_token"))
        open_issues_url = str(kwargs.pop("open_issues_url"))
    except KeyError as exc:  # pragma: no cover - defensive contract guard
        raise TypeError(
            f"Missing required DeepScan policy field: {exc.args[0]}"
        ) from exc
    if kwargs:
        raise TypeError(
            "Unexpected _evaluate_deepscan_policy parameters: "
            f"{', '.join(sorted(kwargs))}"
        )
    if policy_mode == "github_check_context":
        return _evaluate_github_check_context(args, github_token)
    return _evaluate_open_issues_mode(open_issues_url, token)


def main() -> int:
    """Run the DeepScan gate and write its report."""
    args = _parse_args()
    token = (args.token or os.environ.get("DEEPSCAN_API_TOKEN", "")).strip()
    github_token = (
        os.environ.get("GITHUB_TOKEN", "").strip()
        or os.environ.get("GH_TOKEN", "").strip()
    )
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
