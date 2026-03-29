"""Shared Codacy zero-gate CLI structures and formatting helpers."""

from __future__ import absolute_import

import argparse
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

from scripts.quality.common import utc_timestamp, write_report

CODACY_API_BASE = "https://api.codacy.com"
CODACY_APP_API_BASE = "https://app.codacy.com/api/v3"
JSON_ACCEPT_HEADER = "application/json"


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
    """Return the input mapping or an empty dictionary."""
    return value if isinstance(value, dict) else {}


def _preferred_text(*values: Any) -> str:
    """Return the first non-empty textual value from the provided arguments."""
    for value in values:
        text = str("" if value is None else value).strip()
        if text:
            return text
    return ""


def _build_retry_config(
    query: CodacyQuery,
    provider_candidates: Sequence[str],
    *,
    attempts: int,
    pending_fn: CodacyPendingFn,
    sleep_seconds: float = 5.0,
) -> CodacyRetryConfig:
    """Build the retry configuration for one Codacy zero-gate lookup."""
    return CodacyRetryConfig(
        provider_candidates=tuple(provider_candidates),
        attempts=max(1, attempts),
        pending_fn=pending_fn,
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
    if pull_request:
        return pull_request_url
    return (
        f"{CODACY_API_BASE}/api/v3/analysis/organizations/"
        f"{provider}/{owner}/repositories/{repo}/issues/search?{query}"
    )


def build_repository_analysis_url(provider: str, owner: str, repo: str) -> str:
    """Build the public repository analysis endpoint for one Codacy project."""
    return (
        f"{CODACY_APP_API_BASE}/analysis/organizations/"
        f"{provider}/{owner}/repositories/{repo}"
    )


def build_pull_request_analysis_url(
    provider: str,
    owner: str,
    repo: str,
    pull_request: str,
) -> str:
    """Build the public analysis endpoint for one Codacy pull request."""
    return (
        f"{CODACY_APP_API_BASE}/analysis/organizations/"
        f"{provider}/{owner}/repositories/{repo}"
        f"/pull-requests/{pull_request}"
    )


def _request_mode(query: CodacyQuery) -> Tuple[str, Dict[str, Any] | None]:
    """Return the Codacy HTTP method and request body for the selected scope."""
    if query.pull_request:
        return "GET", None
    if query.sha:
        return "POST", {"commitUuid": query.sha}
    return "POST", {}


def _base_query(args: argparse.Namespace, pull_request: str) -> CodacyQuery:
    """Build the normalized Codacy query from CLI arguments."""
    return CodacyQuery(
        provider=args.provider,
        owner=urllib.parse.quote(args.owner.strip(), safe=""),
        repo=urllib.parse.quote(args.repo.strip(), safe=""),
        pull_request=pull_request,
        sha=_preferred_text(getattr(args, "sha", "")).lower(),
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
    lines.extend([f"- {item}" for item in payload.get("findings", [])] or ["- None"])
    return "\n".join(lines) + "\n"


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
