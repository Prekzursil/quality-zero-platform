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
SCOPED_ANALYSIS_RETRY_ATTEMPTS = 8


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assert SonarCloud has zero open issues and a passing quality gate.")
    parser.add_argument("--project-key", required=True)
    parser.add_argument("--token", default="")
    parser.add_argument("--policy-mode", default="ratchet")
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


def load_sonar_findings_with_retry(*args: Any, **kwargs: Any) -> Tuple[int, str, List[str]]:
    if len(args) != 2:
        raise TypeError("load_sonar_findings_with_retry expects argparse namespace and auth header")
    namespace, auth = args
    fetch_fn = kwargs.pop("fetch_fn", _load_sonar_findings)
    attempts = int(kwargs.pop("attempts", SCOPED_ANALYSIS_RETRY_ATTEMPTS))
    sleep_seconds = float(kwargs.pop("sleep_seconds", 5.0))
    if kwargs:
        raise TypeError(f"Unexpected load_sonar_findings_with_retry parameters: {', '.join(sorted(kwargs))}")
    retry_budget = max(1, attempts)
    open_issues = 0
    quality_gate = "UNKNOWN"
    findings: List[str] = []
    for attempt in range(retry_budget):
        try:
            open_issues, quality_gate, findings = fetch_fn(namespace, auth)
        except (OSError, RuntimeError, ValueError) as exc:
            if not _is_scoped_analysis(namespace):
                raise
            findings = [f"Sonar API request failed: {exc}"]
            if attempt != retry_budget - 1:
                time.sleep(max(0.0, sleep_seconds))
                continue
            return open_issues, quality_gate, findings
        if not findings or not _is_scoped_analysis(namespace):
            return open_issues, quality_gate, findings
        if attempt != retry_budget - 1:
            time.sleep(max(0.0, sleep_seconds))
    return open_issues, quality_gate, findings


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
