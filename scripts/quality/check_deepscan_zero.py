#!/usr/bin/env python3
"""Check deepscan zero."""

from __future__ import absolute_import

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import (
    github_commit_status_payload,
    utc_timestamp,
    write_report,
)
from scripts.security_helpers import load_json_https, normalize_https_url

TOTAL_KEYS = {"total", "totalItems", "total_items", "count", "hits", "open_issues"}
DEEPSCAN_STATUS_CONTEXT = "DeepScan"


@dataclass(frozen=True)
class DeepScanEvaluationInputs:
    """Describe the resolved inputs needed for one DeepScan evaluation."""

    token: str
    github_token: str
    policy_mode: str
    open_issues_url: str


def _parse_args() -> argparse.Namespace:
    """Handle parse args."""
    parser = argparse.ArgumentParser(
        description="Assert DeepScan has zero open issues."
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
    """Handle event name."""
    return os.environ.get("EVENT_NAME", "").strip()


def _nested_payload_values(payload: Any) -> List[Any]:
    """Handle nested payload values."""
    if isinstance(payload, dict):
        return list(payload.values())
    if isinstance(payload, list):
        return list(payload)
    return []


def extract_total_open(payload: Any) -> int | None:
    """Handle extract total open."""
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
    """Handle request json."""
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
    """Handle github status payload."""
    return github_commit_status_payload(repo, sha, token)


def _render_md(payload: Mapping[str, Any]) -> str:
    """Handle render md."""
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
    """Handle policy mode."""
    return (
        args.policy_mode or os.environ.get("DEEPSCAN_POLICY_MODE", "")
    ).strip() or "github_check_context"


def _github_repo(args: argparse.Namespace) -> str:
    """Handle github repo."""
    return (
        args.repo
        or os.environ.get("REPO_SLUG", "")
        or os.environ.get("GITHUB_REPOSITORY", "")
    ).strip()


def _github_sha(args: argparse.Namespace) -> str:
    """Handle github sha."""
    return (
        args.sha or os.environ.get("TARGET_SHA", "") or os.environ.get("GITHUB_SHA", "")
    ).strip()


def _validate_github_check_context_inputs(
    github_token: str, repo: str, sha: str
) -> List[str]:
    """Handle validate github check context inputs."""
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
    """Handle validate open issues mode inputs."""
    findings: List[str] = []
    if not token:
        findings.append("DEEPSCAN_API_TOKEN is missing.")
    if not open_issues_url:
        findings.append("DEEPSCAN_OPEN_ISSUES_URL is missing.")
    return findings


def _validate_deepscan_inputs(*args: Any, **kwargs: Any) -> List[str]:
    """Handle validate deepscan inputs."""
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
    """Handle normalized open issues url."""
    return normalize_https_url(open_issues_url, allowed_host_suffixes={"deepscan.io"})


def _evaluate_deepscan_open_issues(
    open_issues_url: str, token: str
) -> Tuple[int | None, List[str]]:
    """Handle evaluate deepscan open issues."""
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
    """Handle find github status."""
    for item in payload.get("statuses", []) or []:
        if str(item.get("context") or "").strip() == context:
            return item
    return None


def _status_target_url(status: Dict[str, Any] | None) -> str:
    """Handle status target url."""
    return str((status or {}).get("target_url") or "").strip()


def _status_findings(status: Dict[str, Any] | None, context: str) -> List[str]:
    """Handle status findings."""
    if status is None:
        return [f"{context} GitHub status context is missing."]

    state = str(status.get("state") or "").strip()
    if state == "success":
        return []
    return [f"{context} GitHub status is {state or 'unknown'} (expected success)."]


def _evaluate_github_check_context(
    args: argparse.Namespace, token: str
) -> Tuple[int | None, str, List[str]]:
    """Handle evaluate github check context."""
    payload = _github_status_payload(_github_repo(args), _github_sha(args), token)
    context = str(
        getattr(args, "github_context", DEEPSCAN_STATUS_CONTEXT)
        or DEEPSCAN_STATUS_CONTEXT
    )
    status = _find_github_status(payload, context)
    if status is None and _event_name() in {"push", "workflow_dispatch"}:
        return 0, "", []
    findings = _status_findings(status, context)
    open_issues = 0 if not findings else None
    return open_issues, _status_target_url(status), findings


def _evaluate_open_issues_mode(
    open_issues_url: str, token: str
) -> Tuple[int | None, str, List[str]]:
    """Handle evaluate open issues mode."""
    normalized_url = _normalized_open_issues_url(open_issues_url)
    open_issues, findings = _evaluate_deepscan_open_issues(normalized_url, token)
    return open_issues, normalized_url, findings


def _evaluate_deepscan_policy(
    args: argparse.Namespace, *call_args: Any, **kwargs: Any
) -> Tuple[int | None, str, List[str]]:
    """Handle evaluate deepscan policy."""
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


def _deepscan_inputs(args: argparse.Namespace) -> DeepScanEvaluationInputs:
    """Return the resolved token inputs for one DeepScan run."""
    return DeepScanEvaluationInputs(
        token=(args.token or os.environ.get("DEEPSCAN_API_TOKEN", "")).strip(),
        github_token=(
            os.environ.get("GITHUB_TOKEN", "").strip()
            or os.environ.get("GH_TOKEN", "").strip()
        ),
        policy_mode=_policy_mode(args),
        open_issues_url=os.environ.get("DEEPSCAN_OPEN_ISSUES_URL", "").strip(),
    )


def _evaluate_deepscan_args(
    args: argparse.Namespace,
    inputs: DeepScanEvaluationInputs,
) -> Tuple[int | None, str, List[str], str]:
    """Return the DeepScan gate result for one invocation."""
    findings = _validate_deepscan_inputs(
        token=inputs.token,
        policy_mode=inputs.policy_mode,
        open_issues_url=inputs.open_issues_url,
        github_token=inputs.github_token,
        repo=_github_repo(args),
        sha=_github_sha(args),
    )
    open_issues: int | None = None
    source_url = inputs.open_issues_url
    status = "fail"
    if findings:
        return open_issues, source_url, findings, status
    try:
        open_issues, source_url, findings = _evaluate_deepscan_policy(
            args,
            policy_mode=inputs.policy_mode,
            token=inputs.token,
            github_token=inputs.github_token,
            open_issues_url=inputs.open_issues_url,
        )
        status = "pass" if not findings else "fail"
    except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
        findings.append(f"DeepScan API request failed: {exc}")
    return open_issues, source_url, findings, status


def main() -> int:
    """Handle main."""
    args = _parse_args()
    inputs = _deepscan_inputs(args)
    open_issues, source_url, findings, status = _evaluate_deepscan_args(args, inputs)

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
