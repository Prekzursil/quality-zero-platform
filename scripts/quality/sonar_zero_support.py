"""Shared helpers used by the Sonar zero gate wrappers."""

from __future__ import absolute_import

from typing import Any, Callable, Dict, Iterable, List, Mapping, Tuple


PendingFn = Callable[[Any, str], str | None]
RequestFn = Callable[[str, str], Dict[str, Any]]
RetryFetchFn = Callable[[Any, str], Tuple[int, str, List[str]]]
SleepFn = Callable[[float], None]
TextFn = Callable[..., str]


def find_named_entry(
    items: Iterable[Mapping[str, Any]],
    key: str,
    value: str,
    *,
    preferred_text: TextFn,
) -> Mapping[str, Any] | None:
    """Return the first mapping whose named field matches the target value."""
    for item in items:
        if preferred_text(item.get(key)) == value:
            return item
    return None


def load_branch_analysis_revision(
    args: Any,
    auth: str,
    *,
    request_json: RequestFn,
    mapping_or_empty: Callable[[Any], Dict[str, Any]],
    named_entry: Callable[
        [Iterable[Mapping[str, Any]], str, str],
        Mapping[str, Any] | None,
    ],
    preferred_text: TextFn,
    sonar_api_base: str,
    url_quote: Callable[..., str],
) -> str:
    """Load the currently indexed commit SHA for one Sonar branch."""
    payload = request_json(
        f"{sonar_api_base}/api/project_branches/list?project="
        f"{url_quote(args.project_key, safe='')}",
        auth,
    )
    branch_entry = named_entry(
        list(payload.get("branches") or []),
        "name",
        preferred_text(args.branch),
    )
    if branch_entry is None:
        return ""
    commit = mapping_or_empty(branch_entry.get("commit"))
    return preferred_text(commit.get("sha")).lower()


def load_pull_request_analysis_revision(
    args: Any,
    auth: str,
    *,
    request_json: RequestFn,
    mapping_or_empty: Callable[[Any], Dict[str, Any]],
    named_entry: Callable[
        [Iterable[Mapping[str, Any]], str, str],
        Mapping[str, Any] | None,
    ],
    preferred_text: TextFn,
    sonar_api_base: str,
    url_quote: Callable[..., str],
) -> str:
    """Load the currently indexed commit SHA for one Sonar pull request."""
    payload = request_json(
        f"{sonar_api_base}/api/project_pull_requests/list?project="
        f"{url_quote(args.project_key, safe='')}",
        auth,
    )
    pull_request_entry = named_entry(
        list(payload.get("pullRequests") or []),
        "key",
        preferred_text(args.pull_request),
    )
    if pull_request_entry is None:
        return ""
    commit = mapping_or_empty(pull_request_entry.get("commit"))
    return preferred_text(commit.get("sha")).lower()


def scoped_analysis_label(args: Any, *, preferred_text: TextFn) -> str:
    """Return the current Sonar branch or pull-request scope label."""
    if preferred_text(getattr(args, "pull_request", "")):
        return f"pull request {args.pull_request}"
    return f"branch {args.branch}"


def load_scoped_analysis_revision(
    args: Any,
    auth: str,
    *,
    preferred_text: TextFn,
    load_pull_request_revision: Callable[[Any, str], str],
    load_branch_revision: Callable[[Any, str], str],
) -> str:
    """Load the current Sonar analysis SHA for the active scope."""
    if preferred_text(getattr(args, "pull_request", "")):
        return load_pull_request_revision(args, auth)
    return load_branch_revision(args, auth)


def scoped_analysis_pending_message(
    args: Any,
    auth: str,
    *,
    is_scoped_analysis: Callable[[Any], bool],
    target_sha: Callable[[Any], str],
    scope_label: Callable[[Any], str],
    load_revision: Callable[[Any, str], str],
) -> str | None:
    """Return the current Sonar pending-analysis message for the active scope."""
    current_target_sha = target_sha(args)
    if not is_scoped_analysis(args) or not current_target_sha:
        return None
    current_scope_label = scope_label(args)
    revision = load_revision(args, auth)
    if not revision:
        return f"Sonar analysis for {current_scope_label} is not available yet."
    if revision != current_target_sha:
        return (
            f"Sonar analysis for {current_scope_label} is still on {revision[:12]} "
            f"(waiting for {current_target_sha[:12]})."
        )
    return None


def resolve_retry_settings(
    retry_kwargs: Mapping[str, Any],
    *,
    default_fetch_fn: RetryFetchFn,
    default_pending_fn: PendingFn,
    default_attempts: int,
    default_sleep_seconds: float,
) -> Tuple[Any, Any, int, float]:
    """Resolve the retry callbacks and timing budget for one Sonar lookup."""
    fetch_fn = retry_kwargs.get("fetch_fn", default_fetch_fn)
    pending_fn = retry_kwargs.get("pending_fn", default_pending_fn)
    attempts = int(retry_kwargs.get("attempts", default_attempts))
    sleep_seconds = float(
        retry_kwargs.get("sleep_seconds", default_sleep_seconds)
    )
    unexpected = sorted(
        set(retry_kwargs) - {"fetch_fn", "pending_fn", "attempts", "sleep_seconds"}
    )
    if unexpected:
        names = ", ".join(unexpected)
        raise TypeError(
            f"Unexpected load_sonar_findings_with_retry parameters: {names}"
        )
    return fetch_fn, pending_fn, max(1, attempts), max(0.0, sleep_seconds)


def retry_exception_result(
    namespace: Any,
    exc: Exception,
    result: Tuple[int, str],
    *,
    is_scoped_analysis: Callable[[Any], bool],
) -> Tuple[int, str, List[str]]:
    """Return the final retry result after one Sonar request exception."""
    if not is_scoped_analysis(namespace):
        raise exc
    open_issues, quality_gate = result
    findings = [f"Sonar API request failed: {exc}"]
    return open_issues, quality_gate, findings


def pending_analysis_message(
    namespace: Any,
    auth: str,
    pending_fn: PendingFn,
) -> str | None:
    """Return a resilient pending-analysis message from the configured callback."""
    try:
        return pending_fn(namespace, auth)
    except (OSError, RuntimeError, ValueError) as exc:
        return f"Sonar analysis status request failed: {exc}"


def should_retry_scoped_analysis(
    namespace: Any,
    findings: List[str],
    pending_message: str | None,
    *,
    is_scoped_analysis: Callable[[Any], bool],
) -> bool:
    """Return whether the current Sonar scoped analysis should retry."""
    return is_scoped_analysis(namespace) and bool(
        findings or pending_message is not None
    )


def final_retry_findings(
    findings: List[str],
    pending_message: str | None,
) -> List[str]:
    """Append the final pending message to the existing Sonar findings."""
    final_findings = list(findings)
    if pending_message is not None and pending_message not in final_findings:
        final_findings.append(pending_message)
    return final_findings


def load_sonar_findings_with_retry(
    namespace: Any,
    auth: str,
    *,
    fetch_fn: RetryFetchFn,
    pending_fn: PendingFn,
    retry_budget: int,
    sleep_seconds: float,
    retry_exception: Callable[
        [Any, Exception, Tuple[int, str]],
        Tuple[int, str, List[str]],
    ],
    pending_message_fn: Callable[[Any, str, PendingFn], str | None],
    should_retry: Callable[[Any, List[str], str | None], bool],
    final_findings_fn: Callable[[List[str], str | None], List[str]],
    sleep_fn: SleepFn,
) -> Tuple[int, str, List[str]]:
    """Retry Sonar findings while the scoped analysis is still settling."""
    open_issues = 0
    quality_gate = "UNKNOWN"
    findings: List[str] = []
    pending_message: str | None = None
    for attempt in range(retry_budget):
        try:
            open_issues, quality_gate, findings = fetch_fn(namespace, auth)
        except (OSError, RuntimeError, ValueError) as exc:
            if attempt == retry_budget - 1:
                return retry_exception(
                    namespace,
                    exc,
                    (open_issues, quality_gate),
                )
            retry_exception(namespace, exc, (open_issues, quality_gate))
            sleep_fn(max(0.0, sleep_seconds))
            continue
        pending_message = pending_message_fn(namespace, auth, pending_fn)
        if not should_retry(namespace, findings, pending_message):
            return open_issues, quality_gate, findings
        if attempt != retry_budget - 1:
            sleep_fn(max(0.0, sleep_seconds))
    return open_issues, quality_gate, final_findings_fn(findings, pending_message)
