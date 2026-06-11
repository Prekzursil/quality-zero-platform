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
    GITHUB_API_BASE,
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
            if key in TOTAL_KEYS and isinstance(value, int | float):
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


def _commit_pulls(repo: str, sha: str, token: str) -> List[Dict[str, Any]]:
    """List the pull requests associated with one commit.

    The DeepScan GitHub App is a PR-only reporter: it posts the ``DeepScan``
    status on the PR head commit, never on the squash-merge commit produced on
    ``main``. ``GET /repos/<repo>/commits/<sha>/pulls`` resolves that head so
    the gate can re-read the combined status there. The endpoint returns a JSON
    array; any non-list payload is treated as "no associated pulls".
    """
    payload, _ = load_json_https(
        f"{GITHUB_API_BASE}/repos/{repo}/commits/{sha}/pulls",
        allowed_hosts={"api.github.com"},
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "quality-zero-platform",
        },
    )
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


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


def _first_nonempty(*values: str) -> str:
    """Return the first truthy string from ``values``, or an empty string."""
    for value in values:
        if value:
            return value
    return ""


def _policy_mode(args: argparse.Namespace) -> str:
    """Handle policy mode."""
    configured = _first_nonempty(
        args.policy_mode, os.environ.get("DEEPSCAN_POLICY_MODE", "")
    )
    return configured.strip() or "github_check_context"


def _github_repo(args: argparse.Namespace) -> str:
    """Handle github repo."""
    return _first_nonempty(
        args.repo,
        os.environ.get("REPO_SLUG", ""),
        os.environ.get("GITHUB_REPOSITORY", ""),
    ).strip()


def _github_sha(args: argparse.Namespace) -> str:
    """Handle github sha."""
    return _first_nonempty(
        args.sha,
        os.environ.get("TARGET_SHA", ""),
        os.environ.get("GITHUB_SHA", ""),
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


def _validate_deepscan_inputs(
    inputs: DeepScanEvaluationInputs, repo: str, sha: str
) -> List[str]:
    """Return the input-validation findings for one resolved DeepScan run."""
    if inputs.policy_mode == "github_check_context":
        return _validate_github_check_context_inputs(inputs.github_token, repo, sha)
    return _validate_open_issues_mode_inputs(inputs.token, inputs.open_issues_url)


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


def _status_result(
    status: Dict[str, Any], context: str
) -> Tuple[int | None, str, List[str]]:
    """Build the gate result for a found DeepScan status context."""
    findings = _status_findings(status, context)
    open_issues = 0 if not findings else None
    return open_issues, _status_target_url(status), findings


def _missing_status_finding(context: str) -> List[str]:
    """Return the strict-zero finding for a wholly-absent DeepScan status."""
    return [
        f"DeepScan status context '{context}' is missing for the commit "
        "and for its associated pull-request heads. Install the DeepScan "
        "GitHub App on the repository so each pull request publishes a "
        "DeepScan check, or set DEEPSCAN_POLICY_MODE=open_issues + "
        "DEEPSCAN_OPEN_ISSUES_URL to query the API directly.",
    ]


def _pr_head_status_result(
    repo: str, sha: str, token: str, context: str
) -> Tuple[int | None, str, List[str]] | None:
    """Re-read the DeepScan status on each PR head for a status-less commit.

    The DeepScan App posts the ``DeepScan`` status on the PR head, never on
    the squash-merge commit. ``/commits/<sha>/pulls`` resolves those heads;
    the first head that carries a ``DeepScan`` status determines the gate
    outcome (success → pass, any other state → propagate the finding).
    Returns ``None`` when no associated head carries a DeepScan status, so the
    caller emits the strict-zero "missing" finding.
    """
    for pull in _commit_pulls(repo, sha, token):
        head_sha = str((pull.get("head") or {}).get("sha") or "").strip()
        if not head_sha:
            continue
        head_payload = _github_status_payload(repo, head_sha, token)
        head_status = _find_github_status(head_payload, context)
        if head_status is not None:
            return _status_result(head_status, context)
    return None


def _evaluate_github_check_context(
    args: argparse.Namespace, token: str
) -> Tuple[int | None, str, List[str]]:
    """Evaluate the DeepScan GitHub check status for the current commit.

    Strict-zero contract: when no DeepScan status is published for the
    SHA, the gate must red-block instead of pass-by-default. The previous
    silent-pass branch (`status is None and event in {push,workflow_dispatch}`)
    masked repos that never integrated with DeepScan — the user
    explicitly called this out: "make sure that depscan properly red
    gated blocked on both PR and main, on this repo and the rest of them
    as well and especially on QZP repo".

    The DeepScan App is a PR-only reporter, so the squash-merge commit on
    ``main`` carries no ``DeepScan`` status. Before red-blocking we fall back
    to the commit's associated pull-request heads (where DeepScan does post)
    and only emit the missing finding when DeepScan is absent on the merge
    commit AND on every PR head.
    """
    repo = _github_repo(args)
    sha = _github_sha(args)
    payload = _github_status_payload(repo, sha, token)
    context = str(
        getattr(args, "github_context", DEEPSCAN_STATUS_CONTEXT)
        or DEEPSCAN_STATUS_CONTEXT
    )
    status = _find_github_status(payload, context)
    if status is not None:
        return _status_result(status, context)
    fallback = _pr_head_status_result(repo, sha, token, context)
    if fallback is not None:
        return fallback
    return None, "", _missing_status_finding(context)


def _evaluate_open_issues_mode(
    open_issues_url: str, token: str
) -> Tuple[int | None, str, List[str]]:
    """Handle evaluate open issues mode."""
    normalized_url = _normalized_open_issues_url(open_issues_url)
    open_issues, findings = _evaluate_deepscan_open_issues(normalized_url, token)
    return open_issues, normalized_url, findings


def _evaluate_deepscan_policy(
    args: argparse.Namespace, inputs: DeepScanEvaluationInputs
) -> Tuple[int | None, str, List[str]]:
    """Dispatch one DeepScan evaluation to the configured policy mode."""
    if inputs.policy_mode == "github_check_context":
        return _evaluate_github_check_context(args, inputs.github_token)
    return _evaluate_open_issues_mode(inputs.open_issues_url, inputs.token)


def _deepscan_inputs(args: argparse.Namespace) -> DeepScanEvaluationInputs:
    """Return the resolved token inputs for one DeepScan run."""
    return DeepScanEvaluationInputs(
        token=_first_nonempty(
            args.token, os.environ.get("DEEPSCAN_API_TOKEN", "")
        ).strip(),
        github_token=_first_nonempty(
            os.environ.get("GITHUB_TOKEN", "").strip(),
            os.environ.get("GH_TOKEN", "").strip(),
        ),
        policy_mode=_policy_mode(args),
        open_issues_url=os.environ.get("DEEPSCAN_OPEN_ISSUES_URL", "").strip(),
    )


def _evaluate_deepscan_args(
    args: argparse.Namespace,
    inputs: DeepScanEvaluationInputs,
) -> Tuple[int | None, str, List[str], str]:
    """Return the DeepScan gate result for one invocation."""
    findings = _validate_deepscan_inputs(inputs, _github_repo(args), _github_sha(args))
    open_issues: int | None = None
    source_url = inputs.open_issues_url
    status = "fail"
    if findings:
        return open_issues, source_url, findings, status
    try:
        open_issues, source_url, findings = _evaluate_deepscan_policy(args, inputs)
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
