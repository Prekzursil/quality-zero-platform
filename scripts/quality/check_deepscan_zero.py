#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report
from scripts.security_helpers import normalize_https_url


TOTAL_KEYS = {"total", "totalItems", "total_items", "count", "hits", "open_issues"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assert DeepScan has zero open issues.")
    parser.add_argument("--token", default="")
    parser.add_argument("--out-json", default="deepscan-zero/deepscan.json")
    parser.add_argument("--out-md", default="deepscan-zero/deepscan.md")
    return parser.parse_args()


def extract_total_open(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in TOTAL_KEYS and isinstance(value, (int, float)):
                return int(value)
        for nested in payload.values():
            total = extract_total_open(nested)
            if total is not None:
                return total
    elif isinstance(payload, list):
        for nested in payload:
            total = extract_total_open(nested)
            if total is not None:
                return total
    return None


def _request_json(url: str, token: str) -> dict[str, Any]:
    safe_url = normalize_https_url(url, allowed_host_suffixes={"deepscan.io"})
    request = urllib.request.Request(
        safe_url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "quality-zero-platform",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _render_md(payload: dict) -> str:
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


def main() -> int:
    args = _parse_args()
    token = (args.token or os.environ.get("DEEPSCAN_API_TOKEN", "")).strip()
    open_issues_url = os.environ.get("DEEPSCAN_OPEN_ISSUES_URL", "").strip()
    findings: list[str] = []
    open_issues: int | None = None

    if not token:
        findings.append("DEEPSCAN_API_TOKEN is missing.")
    if not open_issues_url:
        findings.append("DEEPSCAN_OPEN_ISSUES_URL is missing.")
    else:
        try:
            open_issues_url = normalize_https_url(open_issues_url, allowed_host_suffixes={"deepscan.io"})
        except ValueError as exc:
            findings.append(str(exc))

    status = "fail"
    if not findings:
        try:
            payload = _request_json(open_issues_url, token)
            open_issues = extract_total_open(payload)
            if open_issues is None:
                findings.append("DeepScan response did not include a parseable total issue count.")
            elif open_issues != 0:
                findings.append(f"DeepScan reports {open_issues} open issues (expected 0).")
            status = "pass" if not findings else "fail"
        except Exception as exc:  # pragma: no cover
            findings.append(f"DeepScan API request failed: {exc}")

    payload = {
        "status": status,
        "open_issues": open_issues,
        "open_issues_url": open_issues_url,
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
