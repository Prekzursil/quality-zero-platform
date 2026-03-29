#!/usr/bin/env python3
from __future__ import absolute_import

import argparse
import base64
import os
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report
from scripts.security_helpers import load_json_https


SONAR_API_BASE = "https://sonarcloud.io"
SCOPED_ANALYSIS_RETRY_ATTEMPTS = 72


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assert SonarCloud has zero open issues and a passing quality gate.")
    parser.add_argument("--project-key", required=True)
    parser.add_argument("--token", default="")
    parser.add_argument("--policy-mode", default="ratchet")
    parser.add_argument("--sha", default="")
    parser.add_argument("--branch", default="")
    parser.add_argument("--pull-request", default="")
    parser.add_argument("--out-json", default="sonar-zero/sonar.json")
    parser.add_argument("--out-md", default="sonar-zero/sonar.md")
    return parser.parse_args()


def _auth_header(token: str) -> str:
    return "Basic " + base64.b64encode(f"{token}:".encode("utf-8")).decode("ascii")


def _request_json(url: str, auth_header: str) -> Dict[str, Any]:
    payload, _ = load_json_https(
        url.rstrip("/"),
        allowed_host_suffixes={"sonarcloud.io"},
        headers={
            "Accept": "application/json",
            "Authorization": auth_header,
            "User-Agent": "quality-zero-platform",
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected SonarCloud API response payload")
    return payload


def _render_md(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Sonar Zero Gate",
        "",
        f"- Status: `{payload['status']}`",
        f"- Project: `{payload['project_key']}`",
        f"- Open issues: `{payload.get('open_issues')}`",
        f"- Quality gate: `{payload.get('quality_gate')}`",
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
        "",
        "## Findings",
    ]
    lines.extend([f"- {item}" for item in payload.get("findings", [])] or ["- None"])
    return "\n".join(lines) + "\n"


def _build_sonar_query(project_key: str, *, branch: str, pull_request: str) -> Dict[str, str]:
    query = {"projectKey": project_key}
    if branch:
        query["branch"] = branch
    if pull_request:
        query["pullRequest"] = pull_request
    return query


def _load_open_issues(args: argparse.Namespace, auth: str) -> int:
    issues_query = {
        "componentKeys": args.project_key,
        "resolved": "false",
        "ps": "1",
    }
    if args.branch:
        issues_query["branch"] = args.branch
    if args.pull_request:
        issues_query["pullRequest"] = args.pull_request
    issues_payload = _request_json(
        f"{SONAR_API_BASE}/api/issues/search?{urllib.parse.urlencode(issues_query)}",
        auth,
    )
    return int((issues_payload.get("paging") or {}).get("total") or 0)


def _load_quality_gate(args: argparse.Namespace, auth: str) -> str:
    gate_query = _build_sonar_query(
        args.project_key,
        branch=args.branch,
        pull_request=args.pull_request,
    )
    gate_payload = _request_json(
        f"{SONAR_API_BASE}/api/qualitygates/project_status?{urllib.parse.urlencode(gate_query)}",
        auth,
    )
    return str((gate_payload.get("projectStatus") or {}).get("status") or "UNKNOWN")


def _load_sonar_findings(args: argparse.Namespace, auth: str) -> Tuple[int, str, List[str]]:
    open_issues = _load_open_issues(args, auth)
    quality_gate = _load_quality_gate(args, auth)
    findings: List[str] = []
    ratchet_scoped = getattr(args, "policy_mode", "ratchet") == "ratchet" and _is_scoped_analysis(args)
    if open_issues != 0 and not ratchet_scoped:
        findings.append(f"Sonar reports {open_issues} open issues (expected 0).")
    if quality_gate != "OK":
        findings.append(f"Sonar quality gate status is {quality_gate} (expected OK).")
    return open_issues, quality_gate, findings


def _is_scoped_analysis(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "branch", "").strip() or getattr(args, "pull_request", "").strip())


def _target_sha(args: argparse.Namespace) -> str:
    return str(getattr(args, "sha", "") or "").strip().lower()


def _find_named_entry(items: List[Mapping[str, Any]], key: str, value: str) -> Mapping[str, Any] | None:
    for item in items:
        if str(item.get(key) or "").strip() == value:
            return item
    return None


def _load_branch_analysis_revision(args: argparse.Namespace, auth: str) -> str:
    payload = _request_json(
        f"{SONAR_API_BASE}/api/project_branches/list?project={urllib.parse.quote(args.project_key, safe='')}",
        auth,
    )
    branch_entry = _find_named_entry(list(payload.get("branches") or []), "name", str(args.branch or "").strip())
    if branch_entry is None:
        return ""
    commit = branch_entry.get("commit") or {}
    return str(commit.get("sha") or "").strip().lower()


def _load_pull_request_analysis_revision(args: argparse.Namespace, auth: str) -> str:
    payload = _request_json(
        f"{SONAR_API_BASE}/api/project_pull_requests/list?project={urllib.parse.quote(args.project_key, safe='')}",
        auth,
    )
    pull_request_entry = _find_named_entry(
        list(payload.get("pullRequests") or []),
        "key",
        str(args.pull_request or "").strip(),
    )
    if pull_request_entry is None:
        return ""
    commit = pull_request_entry.get("commit") or {}
    return str(commit.get("sha") or "").strip().lower()


def _scoped_analysis_pending_message(args: argparse.Namespace, auth: str) -> str | None:
    target_sha = _target_sha(args)
    if not _is_scoped_analysis(args) or not target_sha:
        return None

    if str(getattr(args, "pull_request", "") or "").strip():
        revision = _load_pull_request_analysis_revision(args, auth)
        scope_label = f"pull request {args.pull_request}"
    else:
        revision = _load_branch_analysis_revision(args, auth)
        scope_label = f"branch {args.branch}"

    if not revision:
        return f"Sonar analysis for {scope_label} is not available yet."
    if revision != target_sha:
        return (
            f"Sonar analysis for {scope_label} is still on {revision[:12]} "
            f"(waiting for {target_sha[:12]})."
        )
    return None


def _resolve_retry_settings(
    retry_kwargs: Mapping[str, Any],
) -> Tuple[Any, Any, int, float]:
    fetch_fn = retry_kwargs.get("fetch_fn", _load_sonar_findings)
    pending_fn = retry_kwargs.get("pending_fn", _scoped_analysis_pending_message)
    attempts = int(retry_kwargs.get("attempts", SCOPED_ANALYSIS_RETRY_ATTEMPTS))
    sleep_seconds = float(retry_kwargs.get("sleep_seconds", 5.0))
    unexpected = sorted(set(retry_kwargs) - {"fetch_fn", "pending_fn", "attempts", "sleep_seconds"})
    if unexpected:
        raise TypeError(f"Unexpected load_sonar_findings_with_retry parameters: {', '.join(unexpected)}")
    return fetch_fn, pending_fn, max(1, attempts), max(0.0, sleep_seconds)


def _retry_exception_result(
    namespace: argparse.Namespace,
    exc: Exception,
    result: Tuple[int, str],
) -> Tuple[int, str, List[str]]:
    if not _is_scoped_analysis(namespace):
        raise exc
    open_issues, quality_gate = result
    findings = [f"Sonar API request failed: {exc}"]
    return open_issues, quality_gate, findings


def load_sonar_findings_with_retry(*args: Any, **kwargs: Any) -> Tuple[int, str, List[str]]:
    if len(args) != 2:
        raise TypeError("load_sonar_findings_with_retry expects argparse namespace and auth header")
    namespace, auth = args
    fetch_fn, pending_fn, retry_budget, sleep_seconds = _resolve_retry_settings(kwargs)
    open_issues = 0
    quality_gate = "UNKNOWN"
    findings: List[str] = []
    pending_message: str | None = None
    for attempt in range(retry_budget):
        try:
            open_issues, quality_gate, findings = fetch_fn(namespace, auth)
        except (OSError, RuntimeError, ValueError) as exc:
            if attempt == retry_budget - 1:
                return _retry_exception_result(namespace, exc, (open_issues, quality_gate))
            _retry_exception_result(namespace, exc, (open_issues, quality_gate))
            time.sleep(max(0.0, sleep_seconds))
            continue
        try:
            pending_message = pending_fn(namespace, auth)
        except (OSError, RuntimeError, ValueError) as exc:
            pending_message = f"Sonar analysis status request failed: {exc}"
        should_retry = _is_scoped_analysis(namespace) and (bool(findings) or pending_message is not None)
        if not should_retry:
            return open_issues, quality_gate, findings
        if attempt != retry_budget - 1:
            time.sleep(max(0.0, sleep_seconds))
    final_findings = list(findings)
    if pending_message is not None and pending_message not in final_findings:
        final_findings.append(pending_message)
    return open_issues, quality_gate, final_findings


def main() -> int:
    args = _parse_args()
    token = (args.token or os.environ.get("SONAR_TOKEN", "")).strip()
    findings: List[str] = []
    open_issues: int | None = None
    quality_gate: str | None = None

    if not token:
        findings.append("SONAR_TOKEN is missing.")
        status = "fail"
    else:
        try:
            auth = _auth_header(token)
            open_issues, quality_gate, findings = load_sonar_findings_with_retry(args, auth)
            status = "pass" if not findings else "fail"
            if getattr(args, "policy_mode", "ratchet") == "audit":
                status = "pass"
        except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
            findings.append(f"Sonar API request failed: {exc}")
            status = "fail"

    payload = {
        "status": status,
        "project_key": args.project_key,
        "open_issues": open_issues,
        "quality_gate": quality_gate,
        "timestamp_utc": utc_timestamp(),
        "findings": findings,
    }
    return_code = write_report(
        payload,
        out_json=args.out_json,
        out_md=args.out_md,
        default_json="sonar-zero/sonar.json",
        default_md="sonar-zero/sonar.md",
        render_md=_render_md,
    )
    if return_code != 0:
        return return_code
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
