#!/usr/bin/env python3
"""Check required checks."""

from __future__ import absolute_import

import argparse
import os
import sys
import time
from typing import Any, Dict, List, Mapping, Tuple

from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report
from scripts.security_helpers import load_json_https


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the required-context gate."""
    parser = argparse.ArgumentParser(description="Wait for required GitHub contexts and assert they are successful.")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--sha", required=True, help="commit SHA")
    parser.add_argument(
        "--required-context",
        action="append",
        default=[],
        help="Required context name",
    )
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--poll-seconds", type=int, default=20)
    parser.add_argument("--out-json", default="quality-zero-gate/required-checks.json")
    parser.add_argument("--out-md", default="quality-zero-gate/required-checks.md")
    return parser.parse_args()


def _api_get(repo: str, path: str, token: str) -> Dict[str, Any]:
    """Fetch and validate a JSON payload from the GitHub REST API."""
    payload, _ = load_json_https(
        f"https://api.github.com/repos/{repo}/{path.lstrip('/')}",
        allowed_hosts={"api.github.com"},
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "quality-zero-platform",
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected GitHub API response payload")
    return payload


def _context_details(state: str, conclusion: str, source: str) -> Dict[str, str]:
    """Build a normalized status record for a discovered context."""
    return {
        "state": state,
        "conclusion": conclusion,
        "source": source,
    }


def _collect_check_run_contexts(
    check_runs_payload: Dict[str, Any],
) -> Dict[str, Dict[str, str]]:
    """Collect GitHub check-run contexts keyed by their displayed name."""
    contexts: Dict[str, Dict[str, str]] = {}
    for run in check_runs_payload.get("check_runs", []) or []:
        name = str(run.get("name") or "").strip()
        if not name:
            continue
        contexts[name] = _context_details(
            str(run.get("status") or ""),
            str(run.get("conclusion") or ""),
            "check_run",
        )
    return contexts


def _collect_status_contexts(
    status_payload: Dict[str, Any],
) -> Dict[str, Dict[str, str]]:
    """Collect legacy commit-status contexts keyed by their context name."""
    contexts: Dict[str, Dict[str, str]] = {}
    for status in status_payload.get("statuses", []) or []:
        name = str(status.get("context") or "").strip()
        if not name:
            continue
        state = str(status.get("state") or "")
        contexts[name] = _context_details(state, state, "status")
    return contexts


def _collect_contexts(
    check_runs_payload: Dict[str, Any],
    status_payload: Dict[str, Any],
) -> Dict[str, Dict[str, str]]:
    """Merge check-run and commit-status contexts into one lookup table."""
    contexts = _collect_check_run_contexts(check_runs_payload)
    contexts.update(_collect_status_contexts(status_payload))
    return contexts


def _resolve_observed_context(
    context: str,
    contexts: Mapping[str, Dict[str, str]],
) -> Dict[str, str] | None:
    """Resolve a required context by exact name or matrix-job suffix."""
    exact = contexts.get(context)
    if exact:
        return exact
    suffix_matches = [details for name, details in contexts.items() if name.endswith(f" / {context}")]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    return None


def _evaluate_observed_context(
    context: str,
    observed: Dict[str, str] | None,
) -> str | None:
    """Return a failure message when an observed context is not successful."""
    if not observed:
        return None

    failure: str | None = None
    if observed["source"] == "check_run":
        if observed["state"] != "completed":
            failure = f"{context}: status={observed['state']}"
        elif observed["conclusion"] != "success":
            failure = f"{context}: conclusion={observed['conclusion']}"
    elif observed["conclusion"] != "success":
        failure = f"{context}: state={observed['conclusion']}"
    return failure


def _evaluate(
    required: List[str],
    contexts: Dict[str, Dict[str, str]],
) -> Tuple[str, List[str], List[str]]:
    """Return overall gate status plus missing and failed required contexts."""
    missing = [context for context in required if _resolve_observed_context(context, contexts) is None]
    failed = [
        failure
        for context in required
        for failure in [
            _evaluate_observed_context(
                context,
                _resolve_observed_context(context, contexts),
            )
        ]
        if failure
    ]
    return ("pass" if not missing and not failed else "fail", missing, failed)


def _has_in_progress_check_runs(
    required: List[str],
    contexts: Dict[str, Dict[str, str]],
) -> bool:
    """Check whether any required check-run context is still running."""
    return any(
        observed.get("state") != "completed"
        for context in required
        for observed in [_resolve_observed_context(context, contexts)]
        if observed and observed.get("source") == "check_run"
    )


def _collect_payload(
    repo: str,
    sha: str,
    required: List[str],
    token: str,
) -> Dict[str, Any]:
    """Fetch the latest GitHub contexts and evaluate the required set."""
    check_runs = _api_get(repo, f"commits/{sha}/check-runs?per_page=100", token)
    statuses = _api_get(repo, f"commits/{sha}/status", token)
    contexts = _collect_contexts(check_runs, statuses)
    status, missing, failed = _evaluate(required, contexts)
    return {
        "status": status,
        "repo": repo,
        "sha": sha,
        "required": required,
        "missing": missing,
        "failed": failed,
        "contexts": contexts,
        "timestamp_utc": utc_timestamp(),
    }


def _should_keep_polling(required: List[str], payload: Mapping[str, Any]) -> bool:
    """Keep polling until required contexts either pass or settle in failure."""
    if payload.get("status") == "pass":
        return False
    if payload.get("missing"):
        return True
    contexts = payload.get("contexts", {})
    return isinstance(contexts, dict) and _has_in_progress_check_runs(
        required,
        contexts,
    )


def _wait_for_payload(
    args: argparse.Namespace,
    required: List[str],
    token: str,
) -> Dict[str, Any]:
    """Poll GitHub until the required contexts settle or the timeout expires."""
    deadline = time.time() + max(args.timeout_seconds, 1)
    final_payload: Dict[str, Any]
    while time.time() <= deadline:
        final_payload = _collect_payload(args.repo, args.sha, required, token)
        if not _should_keep_polling(required, final_payload):
            break
        time.sleep(max(args.poll_seconds, 1))
    return final_payload


def _render_md(payload: Mapping[str, Any]) -> str:
    """Render a markdown report for the required-context gate result."""
    lines = [
        "# Quality Zero Gate - Required Contexts",
        "",
        f"- Status: `{payload['status']}`",
        f"- Repo/SHA: `{payload['repo']}@{payload['sha']}`",
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
        "",
        "## Missing contexts",
    ]
    lines.extend([f"- `{item}`" for item in payload.get("missing", [])] or ["- None"])
    lines.extend(["", "## Failed contexts"])
    lines.extend([f"- {item}" for item in payload.get("failed", [])] or ["- None"])
    return "\n".join(lines) + "\n"


def main() -> int:
    """Run the required-context gate and write its JSON and markdown reports."""
    args = _parse_args()
    token = (os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")).strip()
    required = [item.strip() for item in args.required_context if item.strip()]
    if not required:
        raise SystemExit("At least one --required-context is required")
    if not token:
        raise SystemExit("GITHUB_TOKEN or GH_TOKEN is required")
    final_payload = _wait_for_payload(args, required, token)

    return_code = write_report(
        final_payload,
        out_json=args.out_json,
        out_md=args.out_md,
        default_json="quality-zero-gate/required-checks.json",
        default_md="quality-zero-gate/required-checks.md",
        render_md=_render_md,
    )
    if return_code != 0:
        return return_code
    return 0 if final_payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
