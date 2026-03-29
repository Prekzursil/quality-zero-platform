#!/usr/bin/env python3
"""Assert that Codacy reports zero open issues for a repository or pull request."""

from __future__ import absolute_import  # skipcq: PY-W2000 - retained for Codacy compatibility.

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report
from scripts.security_helpers import load_json_https


TOTAL_KEYS = {
    "total",
    "totalItems",
    "total_items",
    "count",
    "hits",
    "open_issues",
    "issuesCount",
}
CODACY_API_BASE = "https://api.codacy.com"
CODACY_APP_API_BASE = "https://app.codacy.com/api/v3"
JSON_ACCEPT_HEADER = "application/json"
SCOPED_ANALYSIS_RETRY_ATTEMPTS = 72


@dataclass(frozen=True)
class CodacyStatusResult:
    """Describe one resolved Codacy gate result."""

    status: str
    findings: List[str]
    open_issues: int | None
    pull_request: str


@dataclass(frozen=True)
class CodacyQuery:
    """Describe the repository and optional PR scope for one Codacy query."""

    provider: str
    owner: str
    repo: str
    pull_request: str = ""
    sha: str = ""


CodacyPendingFn = Callable[[CodacyQuery, str], str | None]


@dataclass(frozen=True)
class CodacyRetryConfig:
    """Describe the retry settings for one Codacy zero-gate lookup."""

    provider_candidates: Tuple[str, ...]
    attempts: int
    pending_fn: CodacyPendingFn
    sleep_seconds: float


def _mapping_or_empty(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _preferred_text(*values: Any) -> str:
    for value in values:
        text = str("" if value is None else value).strip()
        if text:
            return text
    return ""


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the Codacy zero gate."""
    parser = argparse.ArgumentParser(
        description="Assert Codacy has zero total open issues."
    )
    parser.add_argument("--provider", default="gh")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--policy-mode", default="ratchet")
    parser.add_argument("--pull-request", default="")
    parser.add_argument("--sha", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--out-json", default="codacy-zero/codacy.json")
    parser.add_argument("--out-md", default="codacy-zero/codacy.md")
    return parser.parse_args()


def _request_json(
    url: str,
    token: str,
    *,
    method: str = "GET",
    data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Request a JSON object from one Codacy API endpoint."""
    body = json.dumps(data).encode("utf-8") if data is not None else None
    payload, _ = load_json_https(
        url.rstrip("/"),
        allowed_host_suffixes={"codacy.com"},
        headers={
            "Accept": JSON_ACCEPT_HEADER,
            "api-token": token,
            "User-Agent": "quality-zero-platform",
            **(
                {"Content-Type": "application/json"}
                if body is not None
                else {}
            ),
        },
        method=method,
        data=body,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Codacy API response payload")
    return payload


def _nested_payload_values(payload: Any) -> List[Any]:
    """Return nested values that may contain an aggregate issue total."""
    if isinstance(payload, dict):
        return list(payload.values())
    if isinstance(payload, list):
        return list(payload)
    return []


def _direct_total_open(payload: Mapping[str, Any]) -> int | None:
    """Read the total issue count from one flat payload."""
    for key, value in payload.items():
        if key in TOTAL_KEYS and isinstance(value, (int, float)):
            return int(value)
    return None


def _dict_total_open(payload: Any) -> int | None:
    """Read the total issue count when the payload is a mapping."""
    if not isinstance(payload, dict):
        return None
    return _direct_total_open(payload)


def extract_total_open(payload: Any) -> int | None:
    """Extract one total issue count from a nested Codacy response payload."""
    if (total := _dict_total_open(payload)) is not None:
        return total
    for nested in _nested_payload_values(payload):
        total = extract_total_open(nested)
        if total is not None:
            return total
    return None


def _render_md(payload: Mapping[str, Any]) -> str:
    """Render the Markdown summary written by the Codacy gate."""
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
    lines.extend(
        [f"- {item}" for item in payload.get("findings", [])] or ["- None"]
    )
    return "\n".join(lines) + "\n"


def _provider_candidates(primary_provider: str) -> List[str]:
    """Return provider aliases to try when querying Codacy."""
    return list(dict.fromkeys([primary_provider, "gh", "github"]))


def _build_retry_config(
    query: CodacyQuery,
    provider_candidates: Sequence[str],
    *,
    pending_fn: CodacyPendingFn | None = None,
    sleep_seconds: float = 5.0,
) -> CodacyRetryConfig:
    attempts = SCOPED_ANALYSIS_RETRY_ATTEMPTS if _preferred_text(query.pull_request, query.sha) else 1
    return CodacyRetryConfig(
        provider_candidates=tuple(provider_candidates),
        attempts=max(1, attempts),
        pending_fn=_analysis_pending_message if pending_fn is None else pending_fn,
        sleep_seconds=max(0.0, sleep_seconds),
    )


def build_issues_url(
    provider: str,
    owner: str,
    repo: str,
    *,
    pull_request: str = "",
) -> str:
    """Build the Codacy issues endpoint for a repository or pull request scope."""
    query = [
        urllib.parse.urlencode({"limit": "1"}),
        urllib.parse.urlencode({"status": "new", "limit": "1"}),
    ][bool(pull_request)]
    pull_request_url = (
        f"{CODACY_APP_API_BASE}/analysis/organizations/"
        f"{provider}/{owner}/repositories/{repo}"
        f"/pull-requests/{pull_request}/issues?{query}"
    )
    return [
        (
            f"{CODACY_API_BASE}/api/v3/analysis/organizations/"
            f"{provider}/{owner}/repositories/{repo}/issues/search?{query}"
        ),
        pull_request_url,
    ][bool(pull_request)]


def build_repository_analysis_url(provider: str, owner: str, repo: str) -> str:
    """Build the public repository analysis endpoint for one Codacy project."""
    return (
        f"{CODACY_APP_API_BASE}/analysis/organizations/"
        f"{provider}/{owner}/repositories/{repo}"
    )


def build_pull_request_analysis_url(provider: str, owner: str, repo: str, pull_request: str) -> str:
    return (
        f"{CODACY_APP_API_BASE}/analysis/organizations/{provider}/{owner}/repositories/{repo}"
        f"/pull-requests/{pull_request}"
    )


def _request_mode(query: CodacyQuery) -> Tuple[str, Dict[str, Any] | None]:
    """Return the Codacy HTTP method and request body for the selected scope."""
    return [("POST", {}), ("GET", None)][bool(query.pull_request)]


def _query_codacy_public_repository_issues(
    provider: str,
    owner: str,
    repo: str,
) -> Tuple[int | None, List[str]]:
    """Query public repository issue totals without using a Codacy API token."""
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


def _query_codacy_provider(
    query: CodacyQuery,
    token: str,
) -> Tuple[int | None, List[str]]:
    """Query Codacy issue totals through the authenticated API."""
    url = build_issues_url(
        query.provider,
        query.owner,
        query.repo,
        pull_request=query.pull_request,
    )
    request_method, request_data = _request_mode(query)
    payload = _request_json(
        url,
        token,
        method=request_method,
        data=request_data,
    )
    return _issue_total_findings(payload)


def _issue_total_findings(payload: Any) -> Tuple[int | None, List[str]]:
    """Convert one Codacy payload into a total and a list of gate findings."""
    open_issues = extract_total_open(payload)
    if open_issues is None:
        return None, [
            "Codacy response did not include a parseable total issue count."
        ]
    if open_issues != 0:
        return open_issues, [
            f"Codacy reports {open_issues} open issues (expected 0)."
        ]
    return open_issues, []


def _fallback_public_issues(
    query: CodacyQuery,
) -> Tuple[int | None, List[str], Exception | None] | None:
    """Fall back to the public repository summary when API auth is unavailable."""
    if query.pull_request:
        return None
    try:
        open_issues, findings = _query_codacy_public_repository_issues(
            query.provider,
            query.owner,
            query.repo,
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
    ) as fallback_exc:  # pragma: no cover
        return None, [], fallback_exc
    return open_issues, findings, None


def _http_error_findings(exc: urllib.error.HTTPError) -> List[str]:
    """Render a standardized finding for one Codacy HTTP error."""
    return [f"Codacy API request failed: HTTP {exc.code}"]


def _unauthorized_http_result(
    exc: urllib.error.HTTPError,
    query: CodacyQuery,
) -> Tuple[int | None, List[str], Exception | None, bool]:
    """Handle unauthorized Codacy API responses with a public fallback when possible."""
    if fallback := _fallback_public_issues(query):
        open_issues, findings, last_exc = fallback
        if last_exc is None:
            return open_issues, findings, None, True
        return None, [], last_exc, False
    return None, _http_error_findings(exc), exc, True


def _handle_codacy_http_error(
    exc: urllib.error.HTTPError,
    query: CodacyQuery,
) -> Tuple[int | None, List[str], Exception | None, bool]:
    """Translate one Codacy HTTP error into the gate's fallback behavior."""
    handler = {
        401: lambda: _unauthorized_http_result(exc, query),
        404: lambda: (None, [], exc, False),
    }.get(exc.code)
    if handler is not None:
        return handler()
    return None, _http_error_findings(exc), exc, True


def _not_found_findings(
    provider_candidates: Any,
    last_exc: Exception | None,
) -> Tuple[int | None, List[str], Exception | None]:
    """Build the finding payload for provider aliases that all returned not found."""
    findings = [
        (
            "Codacy API endpoint was not found for providers: "
            f"{', '.join(provider_candidates)}."
        )
    ]
    if last_exc is not None:
        findings.append(f"Last Codacy API error: {last_exc}")
    return None, findings, last_exc


def _provider_query(base_query: CodacyQuery, provider: str) -> CodacyQuery:
    """Clone the base Codacy query for one provider alias."""
    return CodacyQuery(
        provider=str(provider),
        owner=base_query.owner,
        repo=base_query.repo,
        pull_request=base_query.pull_request,
        sha=base_query.sha,
    )


def _query_codacy_candidate(
    query: CodacyQuery,
    token: Any,
) -> Tuple[int | None, List[str], Exception | None, bool]:
    """Query one provider candidate and normalize recoverable failures."""
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
    """Try each provider alias until one Codacy issue total resolves."""
    last_exc: Exception | None = None
    for provider in provider_candidates:
        query = _provider_query(base_query, str(provider))
        open_issues, findings, last_exc, should_return = _query_codacy_candidate(
            query,
            token,
        )
        if should_return:
            return open_issues, findings, last_exc

    return _not_found_findings(provider_candidates, last_exc)


def _codacy_status(findings: List[str], policy_mode: str) -> str:
    """Resolve the final gate status from the findings and policy mode."""
    if policy_mode == "audit":
        return "pass"
    return "pass" if not findings else "fail"


def _base_query(args: argparse.Namespace, pull_request: str) -> CodacyQuery:
    """Build the normalized Codacy query from CLI arguments."""
    return CodacyQuery(
        provider=args.provider,
        owner=urllib.parse.quote(args.owner.strip(), safe=""),
        repo=urllib.parse.quote(args.repo.strip(), safe=""),
        pull_request=pull_request,
        sha=_preferred_text(getattr(args, "sha", "")).lower(),
    )


def _is_retryable_pr_not_found(
    base_query: CodacyQuery,
    last_exc: Exception | None,
) -> bool:
    """Return whether a missing PR endpoint should be retried after a delay."""
    return (
        bool(base_query.pull_request)
        and isinstance(last_exc, urllib.error.HTTPError)
        and last_exc.code == 404
    )


def _request_analysis_status(url: str, token: str) -> Dict[str, Any]:
    payload, _ = load_json_https(
        url.rstrip("/"),
        allowed_host_suffixes={"codacy.com"},
        headers={
            "Accept": JSON_ACCEPT_HEADER,
            "User-Agent": "quality-zero-platform",
            **({"api-token": token} if token else {}),
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Codacy analysis status payload")
    return payload


def _sha_wait_message(scope_label: str, observed_sha: str, target_sha: str) -> str | None:
    if not observed_sha:
        return f"Codacy analysis for {scope_label} is not available yet."
    if observed_sha != target_sha:
        return (
            f"Codacy analysis for {scope_label} is still on {observed_sha[:12]} "
            f"(waiting for {target_sha[:12]})."
        )
    return None


def _pull_request_pending_message(payload: Dict[str, Any], query: CodacyQuery, target_sha: str) -> str | None:
    if bool(payload.get("isAnalysing")):
        return f"Codacy is still analysing pull request {query.pull_request}."
    pull_request = _mapping_or_empty(payload.get("pullRequest"))
    return _sha_wait_message(
        f"pull request {query.pull_request}",
        _preferred_text(pull_request.get("headCommitSha")).lower(),
        target_sha,
    )


def _repository_pending_message(payload: Dict[str, Any], target_sha: str) -> str | None:
    repository = _mapping_or_empty(payload.get("data"))
    last_analysed_commit = _mapping_or_empty(repository.get("lastAnalysedCommit"))
    pending_message = _sha_wait_message(
        "repository",
        _preferred_text(last_analysed_commit.get("sha")).lower(),
        target_sha,
    )
    if pending_message is not None:
        return pending_message
    if not _preferred_text(last_analysed_commit.get("endedAnalysis")):
        return "Codacy repository analysis has not finished yet."
    return None


def _analysis_pending_message(query: CodacyQuery, token: str) -> str | None:
    target_sha = _preferred_text(query.sha).lower()
    if not target_sha:
        return None

    if query.pull_request:
        payload = _request_analysis_status(
            build_pull_request_analysis_url(query.provider, query.owner, query.repo, query.pull_request),
            token,
        )
        return _pull_request_pending_message(payload, query, target_sha)
    payload = _request_analysis_status(
        build_repository_analysis_url(query.provider, query.owner, query.repo),
        token,
    )
    return _repository_pending_message(payload, target_sha)


def _pending_analysis_message(config: CodacyRetryConfig, query: CodacyQuery, token: str) -> str | None:
    try:
        return config.pending_fn(query, token)
    except (OSError, RuntimeError, ValueError) as exc:
        return f"Codacy analysis status request failed: {exc}"


def _final_retry_findings(
    open_issues: int | None,
    findings: List[str],
    pending_message: str | None,
) -> Tuple[int | None, List[str]]:
    final_findings = list(findings)
    if pending_message is not None and pending_message not in final_findings:
        final_findings.append(pending_message)
    return open_issues, final_findings


def load_codacy_findings_with_retry(
    base_query: CodacyQuery,
    token: str,
    retry_config: CodacyRetryConfig | None = None,
) -> Tuple[int | None, List[str]]:
    """Load Codacy findings, retrying short-lived PR and analysis-lag states."""
    if retry_config is None:
        config = _build_retry_config(base_query, _provider_candidates(base_query.provider))
    else:
        config = retry_config
    open_issues: int | None = None
    findings: List[str] = []
    pending_message: str | None = None
    for attempt in range(config.attempts):
        open_issues, findings, last_exc = _query_codacy_open_issues(
            base_query,
            token,
            list(config.provider_candidates),
        )
        retry_requested = _is_retryable_pr_not_found(base_query, last_exc)
        pending_message = _pending_analysis_message(config, base_query, token)
        if not retry_requested and pending_message is None:
            return open_issues, findings
        if attempt != config.attempts - 1:
            time.sleep(config.sleep_seconds)
    return _final_retry_findings(open_issues, findings, pending_message)


def _resolve_codacy_status(args: argparse.Namespace) -> CodacyStatusResult:
    """Resolve the final Codacy gate status for the current invocation."""
    token = _preferred_text(args.token, os.environ.get("CODACY_API_TOKEN", ""))
    pull_request = _preferred_text(args.pull_request)
    if not token:
        return CodacyStatusResult(
            status="fail",
            findings=["CODACY_API_TOKEN is missing."],
            open_issues=None,
            pull_request=pull_request,
        )

    provider_candidates = _provider_candidates(args.provider)
    base_query = _base_query(args, pull_request)
    open_issues, findings = load_codacy_findings_with_retry(
        base_query,
        token,
        _build_retry_config(base_query, provider_candidates),
    )
    return CodacyStatusResult(
        status=_codacy_status(findings, getattr(args, "policy_mode", "ratchet")),
        findings=findings,
        open_issues=open_issues,
        pull_request=pull_request,
    )


def _build_payload(
    args: argparse.Namespace,
    result: CodacyStatusResult,
) -> Dict[str, Any]:
    """Build the JSON payload persisted by the Codacy zero gate."""
    return {
        "status": result.status,
        "owner": args.owner,
        "repo": args.repo,
        "provider": args.provider,
        "pull_request": result.pull_request if result.pull_request else None,
        "open_issues": result.open_issues,
        "timestamp_utc": utc_timestamp(),
        "findings": result.findings,
    }


def _write_codacy_report(args: argparse.Namespace, payload: Mapping[str, Any]) -> int:
    """Persist the Codacy gate payload in JSON and Markdown formats."""
    return write_report(
        payload,
        out_json=args.out_json,
        out_md=args.out_md,
        default_json="codacy-zero/codacy.json",
        default_md="codacy-zero/codacy.md",
        render_md=_render_md,
    )


def main() -> int:
    """Execute the Codacy zero gate CLI."""
    args = _parse_args()
    result = _resolve_codacy_status(args)
    payload = _build_payload(args, result)
    return_code = _write_codacy_report(args, payload)
    if return_code != 0:
        return return_code
    return 0 if result.status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
