"""Codacy zero-gate pending-message and analysis-status helpers."""

from __future__ import absolute_import

from typing import Any, Dict, List, Tuple

from scripts.quality import codacy_zero_support
from scripts.quality.codacy_zero_support import (
    CodacyIssuePendingDeps,
    CodacyPendingMessageDeps,
    CodacyTextDeps,
)


def request_analysis_status(
    url: str,
    token: str,
    *,
    json_accept_header: str,
    load_json: Any,
) -> Dict[str, Any]:
    """Request the current Codacy analysis-status payload."""
    return codacy_zero_support.request_analysis_status(
        url,
        token,
        json_accept_header=json_accept_header,
        load_json=load_json,
    )


def sha_wait_message(
    scope_label: str,
    observed_sha: str,
    target_sha: str,
) -> str | None:
    """Return the pending message for one observed Codacy analysis SHA."""
    return codacy_zero_support.sha_wait_message(scope_label, observed_sha, target_sha)


def pull_request_pending_message(
    payload: Dict[str, Any],
    query: Any,
    target_sha: str,
    *,
    text_deps: CodacyTextDeps,
) -> str | None:
    """Return the pending status for a Codacy pull-request analysis."""
    return codacy_zero_support.pull_request_pending_message(
        payload,
        query,
        target_sha,
        text_deps=text_deps,
    )


def pull_request_issue_pending_message(
    payload: Dict[str, Any],
    query: Any,
    target_sha: str,
    *,
    deps: CodacyIssuePendingDeps,
) -> str | None:
    """Return the pending status for a Codacy pull-request issues payload."""
    return codacy_zero_support.pull_request_issue_pending_message(
        payload,
        query,
        target_sha,
        deps=deps,
    )


def repository_pending_message(
    payload: Dict[str, Any],
    target_sha: str,
    *,
    text_deps: CodacyTextDeps,
) -> str | None:
    """Return the pending status for the default-branch repository analysis."""
    return codacy_zero_support.repository_pending_message(
        payload,
        target_sha,
        text_deps=text_deps,
    )


def pending_analysis_message(
    config: Any,
    query: Any,
    token: str,
) -> str | None:
    """Return a resilient pending-analysis message from the configured callback."""
    return codacy_zero_support.pending_analysis_message(config, query, token)


def final_retry_findings(
    open_issues: int | None,
    findings: List[str],
    pending_message: str | None,
) -> Tuple[int | None, List[str]]:
    """Append the final pending message to the existing finding list."""
    return codacy_zero_support.final_retry_findings(
        open_issues,
        findings,
        pending_message,
    )
