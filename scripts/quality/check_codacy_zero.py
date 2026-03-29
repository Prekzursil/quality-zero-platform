#!/usr/bin/env python3
"""Assert that Codacy reports zero open issues for a repository or pull request."""

# skipcq: PY-W2000 - retained for Codacy compatibility.
from __future__ import absolute_import

import argparse
import json
import os
import sys
import time
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality import codacy_zero_support
from scripts.quality.codacy_zero_io import (
    JSON_ACCEPT_HEADER,
    CodacyPendingFn,
    CodacyQuery,
    CodacyRetryConfig,
    CodacyStatusResult,
    _render_md as _render_codacy_md,
    _base_query,
    _build_payload,
    _build_retry_config as _build_retry_config_base,
    _mapping_or_empty,
    _preferred_text,
    _request_mode,
    build_issues_url,
    build_pull_request_analysis_url,
    build_repository_analysis_url,
)
from scripts.quality.common import write_report
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
# Codacy's PR-scoped issue view can lag several minutes behind GitHub on large
# or long-lived branches, so the default retry window needs to cover that drift.
SCOPED_ANALYSIS_RETRY_ATTEMPTS = 180


def _render_md(payload: Mapping[str, Any]) -> str:
    """Preserve the legacy markdown helper surface for tests and callers."""
    return _render_codacy_md(payload)


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
            **({"Content-Type": "application/json"} if body is not None else {}),
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
    """Build the retry configuration for one Codacy zero-gate lookup."""
    return _build_retry_config_base(
        provider_candidates,
        attempts=(
            SCOPED_ANALYSIS_RETRY_ATTEMPTS
            if _preferred_text(query.pull_request, query.sha)
            else 1
        ),
        pending_fn=_analysis_pending_message if pending_fn is None else pending_fn,
        sleep_seconds=sleep_seconds,
    )


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
        return None, ["Codacy response did not include a parseable total issue count."]
    if open_issues != 0:
        return open_issues, [f"Codacy reports {open_issues} open issues (expected 0)."]
    return open_issues, []


def _fallback_public_issues(
    query: CodacyQuery,
) -> Tuple[int | None, List[str], Exception | None] | None:
    """Fall back to the public repository summary when API auth is unavailable."""
    return codacy_zero_support.fallback_public_issues(
        query,
        public_issue_query=_query_codacy_public_repository_issues,
    )


_http_error_findings = codacy_zero_support.http_error_findings
_not_found_findings = codacy_zero_support.not_found_findings
_provider_query = codacy_zero_support.provider_query
_is_retryable_pr_not_found = codacy_zero_support.is_retryable_pr_not_found
_sha_wait_message = codacy_zero_support.sha_wait_message
_pending_analysis_message = codacy_zero_support.pending_analysis_message
_final_retry_findings = codacy_zero_support.final_retry_findings


def _unauthorized_http_result(
    exc: urllib.error.HTTPError,
    query: CodacyQuery,
) -> Tuple[int | None, List[str], Exception | None, bool]:
    """Handle unauthorized Codacy API responses with a public fallback when possible."""
    return codacy_zero_support.unauthorized_http_result(
        exc,
        query,
        deps=codacy_zero_support.CodacyHttpErrorDeps(
            public_fallback=_fallback_public_issues,
            error_findings=_http_error_findings,
        ),
    )


def _handle_codacy_http_error(
    exc: urllib.error.HTTPError,
    query: CodacyQuery,
) -> Tuple[int | None, List[str], Exception | None, bool]:
    """Translate one Codacy HTTP error into the gate's fallback behavior."""
    return codacy_zero_support.handle_codacy_http_error(
        exc,
        query,
        deps=codacy_zero_support.CodacyHttpErrorDeps(
            public_fallback=_fallback_public_issues,
            error_findings=_http_error_findings,
        ),
    )


def _query_codacy_candidate(
    query: CodacyQuery,
    token: Any,
) -> Tuple[int | None, List[str], Exception | None, bool]:
    """Query one provider candidate and normalize recoverable failures."""
    return codacy_zero_support.query_codacy_candidate(
        query,
        token,
        deps=codacy_zero_support.CodacyCandidateDeps(
            query_provider=_query_codacy_provider,
            http_error_deps=codacy_zero_support.CodacyHttpErrorDeps(
                public_fallback=_fallback_public_issues,
                error_findings=_http_error_findings,
            ),
        ),
    )


def _query_codacy_open_issues(
    base_query: CodacyQuery,
    token: str,
    provider_candidates: Any,
) -> Tuple[int | None, List[str], Exception | None]:
    """Try each provider alias until one Codacy issue total resolves."""
    return codacy_zero_support.query_codacy_open_issues(
        base_query,
        token,
        provider_candidates,
        deps=codacy_zero_support.CodacyQueryOpenIssuesDeps(
            provider_query_builder=_provider_query,
            query_candidate=_query_codacy_candidate,
            not_found_builder=_not_found_findings,
        ),
    )


def _codacy_status(findings: List[str], policy_mode: str) -> str:
    """Resolve the final gate status from the findings and policy mode."""
    if policy_mode == "audit":
        return "pass"
    return "pass" if not findings else "fail"


def _request_analysis_status(url: str, token: str) -> Dict[str, Any]:
    """Request the current Codacy analysis-status payload."""
    return codacy_zero_support.request_analysis_status(
        url,
        token,
        json_accept_header=JSON_ACCEPT_HEADER,
        load_json=load_json_https,
    )


def _pull_request_pending_message(
    payload: Dict[str, Any],
    query: CodacyQuery,
    target_sha: str,
) -> str | None:
    """Return the pending status for a Codacy pull-request analysis."""
    return codacy_zero_support.pull_request_pending_message(
        payload,
        query,
        target_sha,
        text_deps=codacy_zero_support.CodacyTextDeps(
            mapping_or_empty=_mapping_or_empty,
            preferred_text=_preferred_text,
        ),
    )


def _pull_request_issue_pending_message(
    payload: Dict[str, Any],
    query: CodacyQuery,
    target_sha: str,
) -> str | None:
    """Return the pending status for a Codacy pull-request issues payload."""
    return codacy_zero_support.pull_request_issue_pending_message(
        payload,
        query,
        target_sha,
        deps=codacy_zero_support.CodacyIssuePendingDeps(
            text_deps=codacy_zero_support.CodacyTextDeps(
                mapping_or_empty=_mapping_or_empty,
                preferred_text=_preferred_text,
            ),
            sha_wait_message=_sha_wait_message,
        ),
    )


def _repository_pending_message(payload: Dict[str, Any], target_sha: str) -> str | None:
    """Return the pending status for the default-branch repository analysis."""
    return codacy_zero_support.repository_pending_message(
        payload,
        target_sha,
        text_deps=codacy_zero_support.CodacyTextDeps(
            mapping_or_empty=_mapping_or_empty,
            preferred_text=_preferred_text,
        ),
    )


def _analysis_pending_message(query: CodacyQuery, token: str) -> str | None:
    """Return the current pending-analysis message for the active Codacy scope."""
    target_sha = _preferred_text(query.sha).lower()
    if not target_sha:
        return None

    if query.pull_request:
        status_payload = _request_analysis_status(
            build_pull_request_analysis_url(
                query.provider,
                query.owner,
                query.repo,
                query.pull_request,
            ),
            token,
        )
        pending_message = _pull_request_pending_message(
            status_payload,
            query,
            target_sha,
        )
        if pending_message is not None:
            return pending_message
        issues_payload = _request_json(
            build_issues_url(
                query.provider,
                query.owner,
                query.repo,
                pull_request=query.pull_request,
            ),
            token,
        )
        return _pull_request_issue_pending_message(
            issues_payload,
            query,
            target_sha,
        )

    return codacy_zero_support.analysis_pending_message(
        query,
        token,
        deps=codacy_zero_support.CodacyPendingMessageDeps(
            request_status=_request_analysis_status,
            pull_request_analysis_url=build_pull_request_analysis_url,
            repository_analysis_url=build_repository_analysis_url,
            text_deps=codacy_zero_support.CodacyTextDeps(
                mapping_or_empty=_mapping_or_empty,
                preferred_text=_preferred_text,
            ),
        ),
    )


def _commit_scope_fallback(
    base_query: CodacyQuery,
    token: str,
    provider_candidates: Sequence[str],
    findings: Sequence[str],
) -> Tuple[int | None, List[str]] | None:
    """Return a commit-scoped fallback when pull-request-scoped Codacy data is stale."""
    target_sha = _preferred_text(base_query.sha).lower()
    if (
        not target_sha
        or not codacy_zero_support.stale_pull_request_findings(
            base_query.pull_request,
            findings,
        )
    ):
        return None
    open_issues, commit_findings, last_exc = _query_codacy_open_issues(
        CodacyQuery(
            provider=base_query.provider,
            owner=base_query.owner,
            repo=base_query.repo,
            pull_request="",
            sha=target_sha,
        ),
        token,
        provider_candidates,
    )
    if last_exc is not None or open_issues is None:
        return None
    return open_issues, commit_findings


def load_codacy_findings_with_retry(
    base_query: CodacyQuery,
    token: str,
    retry_config: CodacyRetryConfig | None = None,
) -> Tuple[int | None, List[str]]:
    """Load Codacy findings, retrying short-lived PR and analysis-lag states."""
    if retry_config is None:
        config = _build_retry_config(
            base_query,
            _provider_candidates(base_query.provider),
        )
    else:
        config = retry_config
    return codacy_zero_support.load_codacy_findings_with_retry(
        base_query,
        token,
        config,
        deps=codacy_zero_support.CodacyRetryDeps(
            query_open_issues=_query_codacy_open_issues,
            retryable_pr_not_found=_is_retryable_pr_not_found,
            pending_message_fn=_pending_analysis_message,
            final_findings_fn=_final_retry_findings,
            sleep_fn=time.sleep,
        ),
    )


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
    commit_fallback = _commit_scope_fallback(
        base_query,
        token,
        provider_candidates,
        findings,
    )
    if commit_fallback is not None:
        open_issues, findings = commit_fallback
    return CodacyStatusResult(
        status=_codacy_status(findings, getattr(args, "policy_mode", "ratchet")),
        findings=findings,
        open_issues=open_issues,
        pull_request=pull_request,
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
