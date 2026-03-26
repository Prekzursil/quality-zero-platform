#!/usr/bin/env python3
from __future__ import absolute_import

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report
from scripts.security_helpers import load_json_https


TOTAL_KEYS = {"total", "totalItems", "total_items", "count", "hits", "open_issues", "issuesCount"}
CODACY_API_BASE = "https://api.codacy.com"
CODACY_APP_API_BASE = "https://app.codacy.com/api/v3"
JSON_ACCEPT_HEADER = "application/json"
SCOPED_ANALYSIS_RETRY_ATTEMPTS = 24


@dataclass(frozen=True)
class CodacyStatusResult:
    status: str
    findings: List[str]
    open_issues: int | None
    pull_request: str


@dataclass(frozen=True)
class CodacyQuery:
    provider: str
    owner: str
    repo: str
    pull_request: str = ""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assert Codacy has zero total open issues.")
    parser.add_argument("--provider", default="gh")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--policy-mode", default="ratchet")
    parser.add_argument("--pull-request", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--out-json", default="codacy-zero/codacy.json")
    parser.add_argument("--out-md", default="codacy-zero/codacy.md")
    return parser.parse_args()


def _request_json(url: str, token: str, *, method: str = "GET", data: Dict[str, Any] | None = None) -> Dict[str, Any]:
    body = json.dumps(data).encode("utf-8") if data is not None else None
    payload, _ = load_json_https(
        url.rstrip("/"),
        allowed_host_suffixes={"codacy.com"},
        headers={
            "Accept": JSON_ACCEPT_HEADER,
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


def _nested_payload_values(payload: Any) -> List[Any]:
    if isinstance(payload, dict):
        return list(payload.values())
    if isinstance(payload, list):
        return list(payload)
    return []


def _direct_total_open(payload: Mapping[str, Any]) -> int | None:
    for key, value in payload.items():
        if key in TOTAL_KEYS and isinstance(value, (int, float)):
            return int(value)
    return None


def _dict_total_open(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    return _direct_total_open(payload)


def extract_total_open(payload: Any) -> int | None:
    if (total := _dict_total_open(payload)) is not None:
        return total
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


def _provider_candidates(primary_provider: str) -> List[str]:
    return list(dict.fromkeys([primary_provider, "gh", "github"]))


def build_issues_url(provider: str, owner: str, repo: str, *, pull_request: str = "") -> str:
    query = [
        urllib.parse.urlencode({"limit": "1"}),
        urllib.parse.urlencode({"status": "new", "limit": "1"}),
    ][bool(pull_request)]
    return [
        f"{CODACY_API_BASE}/api/v3/analysis/organizations/{provider}/{owner}/repositories/{repo}/issues/search?{query}",
        (
            f"{CODACY_APP_API_BASE}/analysis/organizations/{provider}/{owner}/repositories/{repo}"
            f"/pull-requests/{pull_request}/issues?{query}"
        ),
    ][bool(pull_request)]


def build_repository_analysis_url(provider: str, owner: str, repo: str) -> str:
    return f"{CODACY_APP_API_BASE}/analysis/organizations/{provider}/{owner}/repositories/{repo}"


def _request_mode(query: CodacyQuery) -> Tuple[str, Dict[str, Any] | None]:
    return [("POST", {}), ("GET", None)][bool(query.pull_request)]


def _query_codacy_public_repository_issues(provider: str, owner: str, repo: str) -> Tuple[int | None, List[str]]:
    payload, _ = load_json_https(
        build_repository_analysis_url(provider, owner, repo),
        allowed_host_suffixes={"codacy.com"},
        headers={
            "Accept": JSON_ACCEPT_HEADER,
            "User-Agent": "quality-zero-platform",
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Codacy public repository payload")

    return _issue_total_findings(payload)


def _query_codacy_provider(query: CodacyQuery, token: str) -> Tuple[int | None, List[str]]:
    url = build_issues_url(query.provider, query.owner, query.repo, pull_request=query.pull_request)
    request_method, request_data = _request_mode(query)
    payload = _request_json(url, token, method=request_method, data=request_data)
    return _issue_total_findings(payload)

def _issue_total_findings(payload: Any) -> Tuple[int | None, List[str]]:
    open_issues = extract_total_open(payload)
    if open_issues is None:
        return None, ["Codacy response did not include a parseable total issue count."]
    if open_issues != 0:
        return open_issues, [f"Codacy reports {open_issues} open issues (expected 0)."]
    return open_issues, []


def _fallback_public_issues(query: CodacyQuery) -> Tuple[int | None, List[str], Exception | None] | None:
    if query.pull_request:
        return None
    try:
        open_issues, findings = _query_codacy_public_repository_issues(query.provider, query.owner, query.repo)
    except (OSError, RuntimeError, ValueError) as fallback_exc:  # pragma: no cover
        return None, [], fallback_exc
    return open_issues, findings, None


def _http_error_findings(exc: urllib.error.HTTPError) -> List[str]:
    return [f"Codacy API request failed: HTTP {exc.code}"]


def _unauthorized_http_result(
    exc: urllib.error.HTTPError,
    query: CodacyQuery,
) -> Tuple[int | None, List[str], Exception | None, bool]:
    if fallback := _fallback_public_issues(query):
        open_issues, findings, last_exc = fallback
        if last_exc is None:
            return open_issues, findings, None, True
        return None, [], last_exc, False
    return None, _http_error_findings(exc), exc, True


def _handle_codacy_http_error(exc: urllib.error.HTTPError, query: CodacyQuery) -> Tuple[int | None, List[str], Exception | None, bool]:
    handler = {
        401: lambda: _unauthorized_http_result(exc, query),
        404: lambda: (None, [], exc, False),
    }.get(exc.code)
    if handler is not None:
        return handler()
    return None, _http_error_findings(exc), exc, True


def _not_found_findings(provider_candidates: Any, last_exc: Exception | None) -> Tuple[int | None, List[str], Exception | None]:
    findings = [f"Codacy API endpoint was not found for providers: {', '.join(provider_candidates)}."]
    if last_exc is not None:
        findings.append(f"Last Codacy API error: {last_exc}")
    return None, findings, last_exc


def _provider_query(base_query: CodacyQuery, provider: str) -> CodacyQuery:
    return CodacyQuery(
        provider=str(provider),
        owner=base_query.owner,
        repo=base_query.repo,
        pull_request=base_query.pull_request,
    )


def _query_codacy_candidate(query: CodacyQuery, token: Any) -> Tuple[int | None, List[str], Exception | None, bool]:
    try:
        open_issues, findings = _query_codacy_provider(query, token)
    except urllib.error.HTTPError as exc:
        return _handle_codacy_http_error(exc, query)
    except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
        return None, [f"Codacy API request failed: {exc}"], exc, True
    return open_issues, findings, None, True


def _query_codacy_open_issues(
    base_query: CodacyQuery,
    token: str,
    provider_candidates: Any,
) -> Tuple[int | None, List[str], Exception | None]:
    last_exc: Exception | None = None
    for provider in provider_candidates:
        query = _provider_query(base_query, str(provider))
        open_issues, findings, last_exc, should_return = _query_codacy_candidate(query, token)
        if should_return:
            return open_issues, findings, last_exc

    return _not_found_findings(provider_candidates, last_exc)


def _codacy_status(findings: List[str], policy_mode: str) -> str:
    return "pass" if policy_mode == "audit" or not findings else "fail"


def _base_query(args: argparse.Namespace, pull_request: str) -> CodacyQuery:
    return CodacyQuery(
        provider=args.provider,
        owner=urllib.parse.quote(args.owner.strip(), safe=""),
        repo=urllib.parse.quote(args.repo.strip(), safe=""),
        pull_request=pull_request,
    )


def _is_retryable_pr_not_found(base_query: CodacyQuery, last_exc: Exception | None) -> bool:
    return (
        bool(base_query.pull_request)
        and isinstance(last_exc, urllib.error.HTTPError)
        and last_exc.code == 404
    )


def load_codacy_findings_with_retry(
    base_query: CodacyQuery,
    token: str,
    provider_candidates: List[str],
) -> Tuple[int | None, List[str]]:
    retry_budget = SCOPED_ANALYSIS_RETRY_ATTEMPTS if base_query.pull_request else 1
    for _ in range(max(0, retry_budget - 1)):
        open_issues, findings, last_exc = _query_codacy_open_issues(base_query, token, provider_candidates)
        if not _is_retryable_pr_not_found(base_query, last_exc):
            return open_issues, findings
        time.sleep(5.0)

    open_issues, findings, _ = _query_codacy_open_issues(base_query, token, provider_candidates)
    return open_issues, findings


def _resolve_codacy_status(args: argparse.Namespace) -> CodacyStatusResult:
    token = (args.token or os.environ.get("CODACY_API_TOKEN", "")).strip()
    pull_request = str(args.pull_request or "").strip()
    if not token:
        return CodacyStatusResult(status="fail", findings=["CODACY_API_TOKEN is missing."], open_issues=None, pull_request=pull_request)

    provider_candidates = _provider_candidates(args.provider)
    base_query = _base_query(args, pull_request)
    open_issues, findings = load_codacy_findings_with_retry(base_query, token, provider_candidates)
    return CodacyStatusResult(
        status=_codacy_status(findings, getattr(args, "policy_mode", "ratchet")),
        findings=findings,
        open_issues=open_issues,
        pull_request=pull_request,
    )


def _build_payload(args: argparse.Namespace, result: CodacyStatusResult) -> Dict[str, Any]:
    return {
        "status": result.status,
        "owner": args.owner,
        "repo": args.repo,
        "provider": args.provider,
        "pull_request": result.pull_request or None,
        "open_issues": result.open_issues,
        "timestamp_utc": utc_timestamp(),
        "findings": result.findings,
    }


def _write_codacy_report(args: argparse.Namespace, payload: Mapping[str, Any]) -> int:
    return write_report(
        payload,
        out_json=args.out_json,
        out_md=args.out_md,
        default_json="codacy-zero/codacy.json",
        default_md="codacy-zero/codacy.md",
        render_md=_render_md,
    )


def main() -> int:
    args = _parse_args()
    result = _resolve_codacy_status(args)
    payload = _build_payload(args, result)
    return_code = _write_codacy_report(args, payload)
    if return_code != 0:
        return return_code
    return 0 if result.status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
