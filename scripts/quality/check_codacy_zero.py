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
from typing import Any, Mapping

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report
from scripts.security_helpers import load_json_https


TOTAL_KEYS = {"total", "totalItems", "total_items", "count", "hits", "open_issues"}
CODACY_API_BASE = "https://api.codacy.com"
CODACY_APP_API_BASE = "https://app.codacy.com/api/v3"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assert Codacy has zero total open issues.")
    parser.add_argument("--provider", default="gh")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pull-request", default="")
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


def _render_md(payload: Mapping[str, Any]) -> str:
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


def _provider_candidates(primary_provider: str) -> list[str]:
    return list(dict.fromkeys([primary_provider, "gh", "github"]))


def build_issues_url(provider: str, owner: str, repo: str, *, pull_request: str = "") -> str:
    if pull_request:
        query = urllib.parse.urlencode({"status": "new", "limit": "1"})
        return (
            f"{CODACY_APP_API_BASE}/analysis/organizations/{provider}/{owner}/repositories/{repo}"
            f"/pull-requests/{pull_request}/issues?{query}"
        )

    query = urllib.parse.urlencode({"limit": "1"})
    return f"{CODACY_API_BASE}/api/v3/analysis/organizations/{provider}/{owner}/repositories/{repo}/issues/search?{query}"


def _request_mode(pull_request: str) -> tuple[str, dict[str, Any] | None]:
    if pull_request:
        return "GET", None
    return "POST", {}


def _query_codacy_provider(
    provider: str,
    owner: str,
    repo: str,
    token: str,
    *,
    pull_request: str = "",
) -> tuple[int | None, list[str]]:
    url = build_issues_url(provider, owner, repo, pull_request=pull_request)
    request_method, request_data = _request_mode(pull_request)
    payload = _request_json(url, token, method=request_method, data=request_data)

    open_issues = extract_total_open(payload)
    if open_issues is None:
        return None, ["Codacy response did not include a parseable total issue count."]
    if open_issues != 0:
        return open_issues, [f"Codacy reports {open_issues} open issues (expected 0)."]
    return open_issues, []


def _query_codacy_open_issues(
    owner: str,
    repo: str,
    token: str,
    provider_candidates: list[str],
    *,
    pull_request: str = "",
) -> tuple[int | None, list[str], Exception | None]:
    findings: list[str] = []
    last_exc: Exception | None = None
    for provider in provider_candidates:
        try:
            open_issues, findings = _query_codacy_provider(
                provider,
                owner,
                repo,
                token,
                pull_request=pull_request,
            )
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code == 404:
                continue
            return None, [f"Codacy API request failed: HTTP {exc.code}"], last_exc
        except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
            last_exc = exc
            return None, [f"Codacy API request failed: {exc}"], last_exc

        return open_issues, findings, last_exc

    findings.append(f"Codacy API endpoint was not found for providers: {', '.join(provider_candidates)}.")
    if last_exc is not None:
        findings.append(f"Last Codacy API error: {last_exc}")
    return None, findings, last_exc


def main() -> int:
    args = _parse_args()
    token = (args.token or os.environ.get("CODACY_API_TOKEN", "")).strip()
    owner = urllib.parse.quote(args.owner.strip(), safe="")
    repo = urllib.parse.quote(args.repo.strip(), safe="")
    pull_request = str(args.pull_request or "").strip()
    findings: list[str] = []
    open_issues: int | None = None
    status = "fail"

    if not token:
        findings.append("CODACY_API_TOKEN is missing.")
    else:
        provider_candidates = _provider_candidates(args.provider)
        open_issues, findings, _ = _query_codacy_open_issues(
            owner,
            repo,
            token,
            provider_candidates,
            pull_request=pull_request,
        )
        status = "pass" if not findings else "fail"

    payload = {
        "status": status,
        "owner": args.owner,
        "repo": args.repo,
        "provider": args.provider,
        "pull_request": pull_request or None,
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
