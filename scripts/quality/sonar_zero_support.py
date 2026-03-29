"""Shared helpers used by the Sonar zero gate wrappers."""

from __future__ import absolute_import

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Tuple

PendingFn = Callable[[Any, str], str | None]
RequestFn = Callable[[str, str], Dict[str, Any]]
RetryFetchFn = Callable[[Any, str], Tuple[int, str, List[str]]]
SleepFn = Callable[[float], None]
TextFn = Callable[..., str]
RetryExceptionFn = Callable[
    [Any, Exception, Tuple[int, str]],
    Tuple[int, str, List[str]],
]
PendingMessageFn = Callable[[Any, str, PendingFn], str | None]
ShouldRetryFn = Callable[[Any, List[str], str | None], bool]
FinalFindingsFn = Callable[[List[str], str | None], List[str]]


@dataclass(frozen=True)
class AnalysisRevisionConfig:
    """Configuration needed to resolve one Sonar analysis revision."""

    request_json: RequestFn
    mapping_or_empty: Callable[[Any], Dict[str, Any]]
    named_entry: Callable[
        [Iterable[Mapping[str, Any]], str, str],
        Mapping[str, Any] | None,
    ]
    preferred_text: TextFn
    sonar_api_base: str
    url_quote: Callable[..., str]
    analysis_path: str
    entries_key: str
    entry_key: str
    target_value: str


@dataclass(frozen=True)
class ScopedAnalysisLoaders:
    """Loaders for the active Sonar branch or pull request scope."""

    load_pull_request_revision: Callable[[Any, str], str]
    load_branch_revision: Callable[[Any, str], str]


@dataclass(frozen=True)
class ScopedAnalysisCallbacks:
    """Callbacks used while checking whether a Sonar scope is still settling."""

    is_scoped_analysis: Callable[[Any], bool]
    target_sha: Callable[[Any], str]
    scope_label: Callable[[Any], str]
    load_revision: Callable[[Any, str], str]


@dataclass(frozen=True)
class RetrySettings:
    """Timing and callback defaults for a Sonar retry loop."""

    fetch_fn: RetryFetchFn
    pending_fn: PendingFn
    attempts: int
    sleep_seconds: float


@dataclass(frozen=True)
class RetryHandlers:
    """Callbacks used while retrying a Sonar findings lookup."""

    retry_exception: RetryExceptionFn
    pending_message_fn: PendingMessageFn
    should_retry: ShouldRetryFn
    final_findings_fn: FinalFindingsFn
    sleep_fn: SleepFn


def _load_analysis_revision(
    args: Any,
    auth: str,
    *,
    config: AnalysisRevisionConfig,
) -> str:
    """Load the currently indexed commit SHA for one Sonar scope."""
    payload = config.request_json(
        f"{config.sonar_api_base}/api/{config.analysis_path}?project=" f"{config.url_quote(args.project_key, safe='')}",
        auth,
    )
    entry = config.named_entry(
        list(payload.get(config.entries_key) or []),
        config.entry_key,
        config.preferred_text(config.target_value),
    )
    if entry is None:
        return ""
    commit = config.mapping_or_empty(entry.get("commit"))
    return config.preferred_text(commit.get("sha")).lower()


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
    config: AnalysisRevisionConfig,
) -> str:
    """Load the currently indexed commit SHA for one Sonar branch."""
    return _load_analysis_revision(args, auth, config=config)


def load_pull_request_analysis_revision(
    args: Any,
    auth: str,
    *,
    config: AnalysisRevisionConfig,
) -> str:
    """Load the currently indexed commit SHA for one Sonar pull request."""
    return _load_analysis_revision(args, auth, config=config)


def scoped_analysis_label(args: Any) -> str:
    """Return the current Sonar branch or pull-request scope label."""
    if str(getattr(args, "pull_request", "")).strip():
        return f"pull request {args.pull_request}"
    return f"branch {args.branch}"


def load_scoped_analysis_revision(
    args: Any,
    auth: str,
    *,
    loaders: ScopedAnalysisLoaders,
) -> str:
    """Load the current Sonar analysis SHA for the active scope."""
    if str(getattr(args, "pull_request", "")).strip():
        return loaders.load_pull_request_revision(args, auth)
    return loaders.load_branch_revision(args, auth)


def scoped_analysis_pending_message(
    args: Any,
    auth: str,
    *,
    callbacks: ScopedAnalysisCallbacks,
) -> str | None:
    """Return the current Sonar pending-analysis message for the active scope."""
    current_target_sha = callbacks.target_sha(args)
    if not callbacks.is_scoped_analysis(args) or not current_target_sha:
        return None
    current_scope_label = callbacks.scope_label(args)
    revision = callbacks.load_revision(args, auth)
    if not revision:
        return f"Sonar analysis for {current_scope_label} is not available yet."
    if revision != current_target_sha:
        return f"Sonar analysis for {current_scope_label} is still on {revision[:12]} " f"(waiting for {current_target_sha[:12]})."
    return None


def resolve_retry_settings(
    retry_kwargs: Mapping[str, Any],
    *,
    defaults: RetrySettings,
) -> RetrySettings:
    """Resolve the retry callbacks and timing budget for one Sonar lookup."""
    fetch_fn = retry_kwargs.get("fetch_fn", defaults.fetch_fn)
    pending_fn = retry_kwargs.get("pending_fn", defaults.pending_fn)
    attempts = int(retry_kwargs.get("attempts", defaults.attempts))
    sleep_seconds = float(retry_kwargs.get("sleep_seconds", defaults.sleep_seconds))
    unexpected = sorted(set(retry_kwargs) - {"fetch_fn", "pending_fn", "attempts", "sleep_seconds"})
    if unexpected:
        names = ", ".join(unexpected)
        raise TypeError(f"Unexpected load_sonar_findings_with_retry parameters: {names}")
    return RetrySettings(
        fetch_fn=fetch_fn,
        pending_fn=pending_fn,
        attempts=max(1, attempts),
        sleep_seconds=max(0.0, sleep_seconds),
    )


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
    return is_scoped_analysis(namespace) and bool(findings or pending_message is not None)


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
    settings: RetrySettings,
    handlers: RetryHandlers,
) -> Tuple[int, str, List[str]]:
    """Retry Sonar findings while the scoped analysis is still settling."""
    open_issues = 0
    quality_gate = "UNKNOWN"
    findings: List[str] = []
    pending_message: str | None = None
    for attempt in range(settings.attempts):
        try:
            open_issues, quality_gate, findings = settings.fetch_fn(namespace, auth)
        except (OSError, RuntimeError, ValueError) as exc:
            if attempt == settings.attempts - 1:
                return handlers.retry_exception(
                    namespace,
                    exc,
                    (open_issues, quality_gate),
                )
            handlers.retry_exception(namespace, exc, (open_issues, quality_gate))
            handlers.sleep_fn(max(0.0, settings.sleep_seconds))
            continue
        pending_message = handlers.pending_message_fn(
            namespace,
            auth,
            settings.pending_fn,
        )
        if not handlers.should_retry(namespace, findings, pending_message):
            return open_issues, quality_gate, findings
        if attempt != settings.attempts - 1:
            handlers.sleep_fn(max(0.0, settings.sleep_seconds))
    return (
        open_issues,
        quality_gate,
        handlers.final_findings_fn(
            findings,
            pending_message,
        ),
    )
