#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report
from scripts.security_helpers import load_json_https


TOTAL_KEYS = {"total", "totalItems", "total_items", "count", "hits", "open_issues"}
CODACY_API_BASE = "https://api.codacy.com"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assert Codacy has zero total open issues.")
    parser.add_argument("--provider", default="gh")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--token", default="")
    parser.add_argument("--out-json", default="codacy-zero/codacy.json")
    parser.add_argument("--out-md", default="codacy-zero/codacy.md")
    return parser.parse_args()


def _request_json(url: str, token: str, *, method: str = "GET", data: dict[str, Any] | None = None) -> dict[str, Any]:
    body = json.dumps(data).encode("utf-8") if data is not None else None
    payload, _ = load_json_https(
        url.rstrip("/"),
        allowed_host_suffixes={"codacy.com"},
        headers={
            "Accept": "application/json",
            "api-token": token,
            "User-Agent": "quality-zero-platform",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
        method=method,
        data=body,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Codacy API response payload")
    return payload


def extract_total_open(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in TOTAL_KEYS and isinstance(value, (int, float)):
                return int(value)
        for value in payload.values():
            total = extract_total_open(value)
            if total is not None:
                return total
    elif isinstance(payload, list):
        for item in payload:
            total = extract_total_open(item)
            if total is not None:
                return total
    return None


def _render_md(payload: dict) -> str:
    lines = [
        "# Codacy Zero Gate",
        "",
        f"- Status: `{payload['status']}`",
        f"- Owner/repo: `{payload['owner']}/{payload['repo']}`",
        f"- Open issues: `{payload.get('open_issues')}`",
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
        "",
        "## Findings",
    ]
    lines.extend([f"- {item}" for item in payload.get("findings", [])] or ["- None"])
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _parse_args()
    token = (args.token or os.environ.get("CODACY_API_TOKEN", "")).strip()
    owner = urllib.parse.quote(args.owner.strip(), safe="")
    repo = urllib.parse.quote(args.repo.strip(), safe="")
    findings: list[str] = []
    open_issues: int | None = None

    if not token:
        findings.append("CODACY_API_TOKEN is missing.")
        status = "fail"
    else:
        provider_candidates = list(dict.fromkeys([args.provider, "gh", "github"]))
        query = urllib.parse.urlencode({"limit": "1"})
        last_exc: Exception | None = None
        status = "fail"
        for provider in provider_candidates:
            url = f"{CODACY_API_BASE}/api/v3/analysis/organizations/{provider}/{owner}/repositories/{repo}/issues/search?{query}"
            try:
                payload = _request_json(url, token, method="POST", data={})
                open_issues = extract_total_open(payload)
                if open_issues is None:
                    findings.append("Codacy response did not include a parseable total issue count.")
                elif open_issues != 0:
                    findings.append(f"Codacy reports {open_issues} open issues (expected 0).")
                status = "pass" if not findings else "fail"
                break
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code == 404:
                    continue
                findings.append(f"Codacy API request failed: HTTP {exc.code}")
                break
            except Exception as exc:  # pragma: no cover
                last_exc = exc
                findings.append(f"Codacy API request failed: {exc}")
                break
        else:
            findings.append(f"Codacy API endpoint was not found for providers: {', '.join(provider_candidates)}.")
            if last_exc is not None:
                findings.append(f"Last Codacy API error: {last_exc}")

    payload = {
        "status": status,
        "owner": args.owner,
        "repo": args.repo,
        "provider": args.provider,
        "open_issues": open_issues,
        "timestamp_utc": utc_timestamp(),
        "findings": findings,
    }
    return_code = write_report(
        payload,
        out_json=args.out_json,
        out_md=args.out_md,
        default_json="codacy-zero/codacy.json",
        default_md="codacy-zero/codacy.md",
        render_md=_render_md,
    )
    if return_code != 0:
        return return_code
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
