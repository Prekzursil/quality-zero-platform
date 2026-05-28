"""Codacy pending-analysis message helpers.

Split from :mod:`scripts.quality.codacy_zero_support` so the analysis-status
polling + pending-message construction lives in a cohesive module, keeping each
module's file-level complexity bounded. The public names are re-exported from
``codacy_zero_support`` to preserve the historical import surface.
"""

from __future__ import absolute_import

from dataclasses import dataclass
from typing import Any, Callable, Dict, Tuple

CodacyRequestFn = Callable[[str, str], Dict[str, Any]]
LoadJsonHttpsFn = Callable[..., Tuple[Any, Dict[str, Any]]]
TextFn = Callable[..., str]


@dataclass(frozen=True)
class CodacyTextDeps:
    """Bundle text normalization helpers used by analysis status messages."""

    mapping_or_empty: Callable[[Any], Dict[str, Any]]
    preferred_text: TextFn


@dataclass(frozen=True)
class CodacyPendingMessageDeps:
    """Bundle pending-analysis helpers for Codacy status checks."""

    request_status: CodacyRequestFn
    pull_request_analysis_url: Callable[[str, str, str, str], str]
    repository_analysis_url: Callable[[str, str, str], str]
    text_deps: CodacyTextDeps


@dataclass(frozen=True)
class CodacyIssuePendingDeps:
    """Bundle helpers for Codacy pull-request issue payload checks."""

    text_deps: CodacyTextDeps
    sha_wait_message: Callable[[str, str, str], str | None]


def request_analysis_status(
    url: str,
    token: str,
    *,
    json_accept_header: str,
    load_json: LoadJsonHttpsFn,
) -> Dict[str, Any]:
    """Load the current Codacy analysis status payload."""
    payload, _ = load_json(
        url.rstrip("/"),
        allowed_host_suffixes={"codacy.com"},
        headers={
            "Accept": json_accept_header,
            "User-Agent": "quality-zero-platform",
            **({"api-token": token} if token else {}),
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Codacy analysis status payload")
    return payload


def sha_wait_message(
    scope_label: str,
    observed_sha: str,
    target_sha: str,
) -> str | None:
    """Return the pending message for one observed Codacy analysis SHA."""
    if not observed_sha:
        return f"Codacy analysis for {scope_label} is not available yet."
    if observed_sha != target_sha:
        return (
            f"Codacy analysis for {scope_label} is still on {observed_sha[:12]} "
            f"(waiting for {target_sha[:12]})."
        )
    return None


def pull_request_pending_message(
    payload: Dict[str, Any],
    query: Any,
    target_sha: str,
    *,
    text_deps: CodacyTextDeps,
) -> str | None:
    """Return the pending status for a Codacy pull-request analysis."""
    if bool(payload.get("isAnalysing")):
        return f"Codacy is still analysing pull request {query.pull_request}."
    pull_request = text_deps.mapping_or_empty(payload.get("pullRequest"))
    return sha_wait_message(
        f"pull request {query.pull_request}",
        text_deps.preferred_text(pull_request.get("headCommitSha")).lower(),
        target_sha,
    )


def pull_request_issue_pending_message(
    payload: Dict[str, Any],
    query: Any,
    target_sha: str,
    *,
    deps: CodacyIssuePendingDeps,
) -> str | None:
    """Return the pending status for a Codacy pull-request issues payload."""
    if payload.get("analyzed") is False:
        return (
            f"Codacy issues for pull request {query.pull_request} are not "
            "available yet."
        )

    issue_records = payload.get("data")
    if not isinstance(issue_records, list) or not issue_records:
        return None

    for item in issue_records:
        issue_mapping = deps.text_deps.mapping_or_empty(item)
        commit_issue = deps.text_deps.mapping_or_empty(issue_mapping.get("commitIssue"))
        commit_info = deps.text_deps.mapping_or_empty(commit_issue.get("commitInfo"))
        observed_sha = deps.text_deps.preferred_text(commit_info.get("sha")).lower()
        if observed_sha:
            return deps.sha_wait_message(
                f"pull request {query.pull_request} issues",
                observed_sha,
                target_sha,
            )
    return None


def repository_pending_message(
    payload: Dict[str, Any],
    target_sha: str,
    *,
    text_deps: CodacyTextDeps,
) -> str | None:
    """Return the pending status for the default-branch repository analysis."""
    repository = text_deps.mapping_or_empty(payload.get("data"))
    last_analysed_commit = text_deps.mapping_or_empty(
        repository.get("lastAnalysedCommit")
    )
    pending_message = sha_wait_message(
        "repository",
        text_deps.preferred_text(last_analysed_commit.get("sha")).lower(),
        target_sha,
    )
    if pending_message is not None:
        return pending_message
    if not text_deps.preferred_text(last_analysed_commit.get("endedAnalysis")):
        return "Codacy repository analysis has not finished yet."
    return None


def analysis_pending_message(
    query: Any,
    token: str,
    *,
    deps: CodacyPendingMessageDeps,
) -> str | None:
    """Return the current pending-analysis message for the active Codacy scope."""
    target_sha = deps.text_deps.preferred_text(query.sha).lower()
    if not target_sha:
        return None
    if query.pull_request:
        payload = deps.request_status(
            deps.pull_request_analysis_url(
                query.provider,
                query.owner,
                query.repo,
                query.pull_request,
            ),
            token,
        )
        return pull_request_pending_message(
            payload,
            query,
            target_sha,
            text_deps=deps.text_deps,
        )
    payload = deps.request_status(
        deps.repository_analysis_url(query.provider, query.owner, query.repo),
        token,
    )
    return repository_pending_message(
        payload,
        target_sha,
        text_deps=deps.text_deps,
    )


def pending_analysis_message(
    config: Any,
    query: Any,
    token: str,
) -> str | None:
    """Return a resilient pending-analysis message from the configured callback."""
    try:
        return config.pending_fn(query, token)
    except (OSError, RuntimeError, ValueError) as exc:
        return f"Codacy analysis status request failed: {exc}"
