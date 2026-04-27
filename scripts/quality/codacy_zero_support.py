"""Shared helpers used by the Codacy zero gate wrappers."""

from __future__ import absolute_import

import urllib.error
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Tuple

CodacyCandidateFn = Callable[..., Tuple[int | None, List[str], Exception | None, bool]]
CodacyPendingFn = Callable[[Any, str], str | None]
CodacyQueryBuilderFn = Callable[[Any, str], Any]
CodacyRequestFn = Callable[[str, str], Dict[str, Any]]
CodacySleepFn = Callable[[float], None]
FindingsFn = Callable[
    [Any, Exception | None],
    Tuple[int | None, List[str], Exception | None],
]
IssuesFallbackFn = Callable[
    [Any],
    Tuple[int | None, List[str], Exception | None] | None,
]
IssuesFn = Callable[[Any, str, Any], Tuple[int | None, List[str], Exception | None]]
LoadJsonHttpsFn = Callable[..., Tuple[Any, Dict[str, Any]]]
ProviderFn = Callable[[str, str, str], Tuple[int | None, List[str]]]
QueryProviderFn = Callable[[Any, str], Tuple[int | None, List[str]]]
TextFn = Callable[..., str]


@dataclass(frozen=True)
class CodacyHttpErrorDeps:
    """Bundle Codacy HTTP error handlers into one dependency object."""

    public_fallback: IssuesFallbackFn
    error_findings: Callable[[urllib.error.HTTPError], List[str]]


@dataclass(frozen=True)
class CodacyQueryOpenIssuesDeps:
    """Bundle query helpers for provider-candidate lookup."""

    provider_query_builder: CodacyQueryBuilderFn
    query_candidate: CodacyCandidateFn
    not_found_builder: FindingsFn


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


@dataclass(frozen=True)
class CodacyCandidateDeps:
    """Bundle query helpers for one Codacy provider candidate."""

    query_provider: QueryProviderFn
    http_error_deps: CodacyHttpErrorDeps


@dataclass(frozen=True)
class CodacyRetryDeps:
    """Bundle retry helpers for the Codacy zero-gate loop."""

    query_open_issues: IssuesFn
    retryable_pr_not_found: Callable[[Any, Exception | None], bool]
    pending_message_fn: Callable[[Any, Any, str], str | None]
    final_findings_fn: Callable[
        [int | None, List[str], str | None],
        Tuple[int | None, List[str]],
    ]
    sleep_fn: CodacySleepFn


def fallback_public_issues(
    query: Any,
    *,
    public_issue_query: ProviderFn,
) -> Tuple[int | None, List[str], Exception | None] | None:
    """Fall back to the public repository summary when auth is unavailable."""
    if query.pull_request:
        return None
    try:
        open_issues, findings = public_issue_query(
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


def http_error_findings(exc: urllib.error.HTTPError) -> List[str]:
    """Render a standardized finding for one Codacy HTTP error."""
    return [f"Codacy API request failed: HTTP {exc.code}"]


def unauthorized_http_result(
    exc: urllib.error.HTTPError,
    query: Any,
    *,
    deps: CodacyHttpErrorDeps,
) -> Tuple[int | None, List[str], Exception | None, bool]:
    """Handle unauthorized Codacy responses with a public fallback when possible."""
    if fallback := deps.public_fallback(query):
        open_issues, findings, last_exc = fallback
        if last_exc is None:
            return open_issues, findings, None, True
        return None, [], last_exc, False
    return None, deps.error_findings(exc), exc, True


def handle_codacy_http_error(
    exc: urllib.error.HTTPError,
    query: Any,
    *,
    deps: CodacyHttpErrorDeps,
) -> Tuple[int | None, List[str], Exception | None, bool]:
    """Translate one Codacy HTTP error into the gate's fallback behavior."""
    handler = {
        401: lambda: unauthorized_http_result(
            exc,
            query,
            deps=deps,
        ),
        404: lambda: (None, [], exc, False),
    }.get(exc.code)
    if handler is not None:
        return handler()
    return None, deps.error_findings(exc), exc, True


def not_found_findings(
    provider_candidates: Iterable[Any],
    last_exc: Exception | None,
) -> Tuple[int | None, List[str], Exception | None]:
    """Build the finding payload for provider aliases that all returned 404."""
    message = (
        "Codacy API endpoint was not found for providers: "
        f"{', '.join(str(item) for item in provider_candidates)}."
    )
    findings = [message]
    if last_exc is not None:
        findings.append(f"Last Codacy API error: {last_exc}")
    return None, findings, last_exc


def provider_query(base_query: Any, provider: str) -> Any:
    """Clone the base Codacy query for one provider alias."""
    return type(base_query)(
        provider=str(provider),
        owner=base_query.owner,
        repo=base_query.repo,
        pull_request=base_query.pull_request,
        sha=base_query.sha,
    )


def query_codacy_candidate(
    query: Any,
    token: Any,
    *,
    deps: CodacyCandidateDeps,
) -> Tuple[int | None, List[str], Exception | None, bool]:
    """Query one provider candidate and normalize recoverable failures."""
    try:
        open_issues, findings = deps.query_provider(query, token)
    except urllib.error.HTTPError as exc:
        return handle_codacy_http_error(exc, query, deps=deps.http_error_deps)
    except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
        return None, [f"Codacy API request failed: {exc}"], exc, True
    return open_issues, findings, None, True


def query_codacy_open_issues(
    base_query: Any,
    token: str,
    provider_candidates: Iterable[Any],
    *,
    deps: CodacyQueryOpenIssuesDeps,
) -> Tuple[int | None, List[str], Exception | None]:
    """Try each provider alias until one Codacy issue total resolves."""
    last_exc: Exception | None = None
    for provider in provider_candidates:
        query = deps.provider_query_builder(base_query, str(provider))
        open_issues, findings, last_exc, should_return = deps.query_candidate(
            query,
            token,
        )
        if should_return:
            return open_issues, findings, last_exc
    return deps.not_found_builder(provider_candidates, last_exc)


def is_retryable_pr_not_found(
    base_query: Any,
    last_exc: Exception | None,
) -> bool:
    """Return whether a missing PR endpoint should be retried after a delay."""
    return (
        bool(base_query.pull_request)
        and isinstance(last_exc, urllib.error.HTTPError)
        and last_exc.code == 404
    )


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


def final_retry_findings(
    open_issues: int | None,
    findings: List[str],
    pending_message: str | None,
) -> Tuple[int | None, List[str]]:
    """Append the final pending message to the existing finding list."""
    final_findings = list(findings)
    if pending_message is not None and pending_message not in final_findings:
        final_findings.append(pending_message)
    return open_issues, final_findings


def stale_pull_request_findings(
    pull_request: str,
    findings: Iterable[str],
) -> bool:
    """Return whether Codacy is still serving stale pull-request-scoped data."""
    normalized_pr = str(pull_request).strip()
    if not normalized_pr:
        return False
    prefixes = (
        f"Codacy is still analysing pull request {normalized_pr}.",
        f"Codacy analysis for pull request {normalized_pr} is not available yet.",
        f"Codacy analysis for pull request {normalized_pr} is still on ",
        f"Codacy issues for pull request {normalized_pr} are not available yet.",
        f"Codacy analysis for pull request {normalized_pr} issues is still on ",
    )
    return any(
        any(item.startswith(prefix) for prefix in prefixes) for item in findings
    )


def load_codacy_findings_with_retry(
    base_query: Any,
    token: str,
    retry_config: Any,
    *,
    deps: CodacyRetryDeps,
) -> Tuple[int | None, List[str]]:
    """Load Codacy findings, retrying short-lived PR and analysis-lag states."""
    open_issues: int | None = None
    findings: List[str] = []
    pending_message: str | None = None
    for attempt in range(retry_config.attempts):
        open_issues, findings, last_exc = deps.query_open_issues(
            base_query,
            token,
            list(retry_config.provider_candidates),
        )
        retry_requested = deps.retryable_pr_not_found(base_query, last_exc)
        pending_message = deps.pending_message_fn(retry_config, base_query, token)
        if not retry_requested and pending_message is None:
            return open_issues, findings
        if attempt != retry_config.attempts - 1:
            deps.sleep_fn(retry_config.sleep_seconds)
    return deps.final_findings_fn(open_issues, findings, pending_message)
