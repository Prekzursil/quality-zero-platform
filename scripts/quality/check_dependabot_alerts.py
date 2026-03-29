#!/usr/bin/env python3
"""Check dependabot alerts."""

from __future__ import absolute_import

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report
from scripts.security_helpers import load_json_https

GITHUB_API_BASE = "https://api.github.com"
_NEXT_LINK_RE = re.compile(r'<(?P<url>[^>]+)>;\s*rel="next"')


def _parse_args() -> argparse.Namespace:
    """Handle parse args."""
    parser = argparse.ArgumentParser(
        description="Assert Dependabot alerts meet the configured threshold."
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--token", default="")
    parser.add_argument("--policy", default="zero_critical")
    parser.add_argument("--scope", default="runtime")
    parser.add_argument("--out-json", default="deps-zero/deps.json")
    parser.add_argument("--out-md", default="deps-zero/deps.md")
    return parser.parse_args()


def _request_alerts(repo: str, token: str, *, scope: str) -> List[Dict[str, Any]]:
    """Handle request alerts."""
    ecosystem = "" if scope == "all" else "&scope=runtime"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "quality-zero-platform",
    }
    next_url = f"{GITHUB_API_BASE}/repos/{repo}/dependabot/alerts?state=open&per_page=100{ecosystem}"
    alerts: List[Dict[str, Any]] = []
    while next_url:
        payload, response_headers = load_json_https(
            next_url,
            allowed_hosts={"api.github.com"},
            headers=headers,
        )
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected Dependabot alerts payload")
        alerts.extend(payload)
        link_header = str(response_headers.get("link", "") or "")
        next_match = _NEXT_LINK_RE.search(link_header)
        next_url = next_match.group("url") if next_match else ""
    return alerts


def filter_alerts(alerts: List[Dict[str, Any]], *, policy: str) -> List[Dict[str, Any]]:
    """Handle filter alerts."""
    order = {"critical": 3, "high": 2, "moderate": 1, "medium": 1, "low": 0}
    threshold = {"zero_critical": 3, "zero_high": 2, "zero_any": 0}[policy]
    filtered: List[Dict[str, Any]] = []
    for alert in alerts:
        severity = str(
            (alert.get("security_vulnerability") or {}).get("severity") or ""
        ).lower()
        if order.get(severity, -1) >= threshold:
            filtered.append(alert)
    return filtered


def _render_md(payload: Mapping[str, Any]) -> str:
    """Handle render md."""
    lines = [
        "# Dependency Alerts Gate",
        "",
        f"- Status: `{payload['status']}`",
        f"- Repo: `{payload['repo']}`",
        f"- Open alerts: `{payload['open_alerts']}`",
        f"- Policy: `{payload['policy']}`",
        f"- Scope: `{payload['scope']}`",
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
        "",
        "## Findings",
    ]
    lines.extend([f"- {item}" for item in payload.get("findings", [])] or ["- None"])
    return "\n".join(lines) + "\n"


def main() -> int:
    """Handle main."""
    args = _parse_args()
    token = (
        args.token
        or os.environ.get("GITHUB_TOKEN", "")
        or os.environ.get("GH_TOKEN", "")
    ).strip()
    findings: List[str] = []
    open_alerts: int | None = None
    status = "fail"

    if not token:
        findings.append("GITHUB_TOKEN or GH_TOKEN is required.")
    else:
        alerts = _request_alerts(args.repo, token, scope=args.scope)
        filtered = filter_alerts(alerts, policy=args.policy)
        open_alerts = len(filtered)
        if open_alerts:
            findings.append(
                f"Dependabot reports {open_alerts} open alerts matching policy {args.policy}."
            )
        status = "pass" if not findings else "fail"

    payload = {
        "status": status,
        "repo": args.repo,
        "policy": args.policy,
        "scope": args.scope,
        "open_alerts": open_alerts,
        "timestamp_utc": utc_timestamp(),
        "findings": findings,
    }
    return_code = write_report(
        payload,
        out_json=args.out_json,
        out_md=args.out_md,
        default_json="deps-zero/deps.json",
        default_md="deps-zero/deps.md",
        render_md=_render_md,
    )
    if return_code != 0:
        return return_code
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
