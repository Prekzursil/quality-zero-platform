#!/usr/bin/env python3
"""CLI wrapper for the Sonar zero gate."""

from __future__ import absolute_import

import argparse
import base64
import os
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report
from scripts.quality import sonar_zero_support
from scripts.security_helpers import load_json_https

SONAR_API_BASE = "https://sonarcloud.io"
SCOPED_ANALYSIS_RETRY_ATTEMPTS = 72


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


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the Sonar zero gate."""
    parser = argparse.ArgumentParser(
        description=(
            "Assert SonarCloud has zero open issues and a passing quality gate."
        )
    )
    parser.add_argument("--project-key", required=True)
    parser.add_argument("--token", default="")
    parser.add_argument("--policy-mode", default="ratchet")
    parser.add_argument("--sha", default="")
    parser.add_argument("--branch", default="")
    parser.add_argument("--pull-request", default="")
    parser.add_argument("--out-json", default="sonar-zero/sonar.json")
    parser.add_argument("--out-md", default="sonar-zero/sonar.md")
    return parser.parse_args()


def _auth_header(token: str) -> str:
    """Build the basic-auth header SonarCloud expects for token auth."""
    return "Basic " + base64.b64encode(f"{token}:".encode("utf-8")).decode("ascii")


def _request_json(url: str, auth_header: str) -> Dict[str, Any]:
    """Request one Sonar JSON payload and validate its top-level shape."""
    payload, _ = load_json_https(
        url.rstrip("/"),
        allowed_host_suffixes={"sonarcloud.io"},
        headers={
            "Accept": "application/json",
            "Authorization": auth_header,
            "User-Agent": "quality-zero-platform",
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected SonarCloud API response payload")
    return payload


def _render_md(payload: Mapping[str, Any]) -> str:
    """Render the Markdown summary written by the Sonar gate."""
    lines = [
        "# Sonar Zero Gate",
        "",
        f"- Status: `{payload['status']}`",
        f"- Project: `{payload['project_key']}`",
        f"- Open issues: `{payload.get('open_issues')}`",
        f"- Quality gate: `{payload.get('quality_gate')}`",
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
        "",
        "## Findings",
    ]
    lines.extend([f"- {item}" for item in payload.get("findings", [])] or ["- None"])
    return "\n".join(lines) + "\n"


def _build_sonar_query(
    project_key: str,
    *,
    branch: str,
    pull_request: str,
) -> Dict[str, str]:
    """Build the Sonar query parameters for the selected branch or PR scope."""
    query = {"projectKey": project_key}
    if branch:
        query["branch"] = branch
    if pull_request:
        query["pullRequest"] = pull_request
    return query


def _load_open_issues(args: argparse.Namespace, auth: str) -> int:
    """Load the open-issue count for the selected Sonar scope."""
    issues_query = {
        "componentKeys": args.project_key,
        "resolved": "false",
        "ps": "1",
    }
    if args.branch:
        issues_query["branch"] = args.branch
    if args.pull_request:
        issues_query["pullRequest"] = args.pull_request
    issues_payload = _request_json(
        f"{SONAR_API_BASE}/api/issues/search?{urllib.parse.urlencode(issues_query)}",
        auth,
    )
    paging = _mapping_or_empty(issues_payload.get("paging"))
    total = paging.get("total")
    return int(0 if total is None else total)


def _load_quality_gate(args: argparse.Namespace, auth: str) -> str:
    """Load the Sonar quality-gate status for the selected scope."""
    gate_query = _build_sonar_query(
        args.project_key,
        branch=args.branch,
        pull_request=args.pull_request,
    )
    gate_payload = _request_json(
        f"{SONAR_API_BASE}/api/qualitygates/project_status?"
        f"{urllib.parse.urlencode(gate_query)}",
        auth,
    )
    project_status = _mapping_or_empty(gate_payload.get("projectStatus"))
    return _preferred_text(project_status.get("status"), "UNKNOWN")


def _load_sonar_findings(
    args: argparse.Namespace,
    auth: str,
) -> Tuple[int, str, List[str]]:
    """Load Sonar issue and quality-gate findings for one scope."""
    open_issues = _load_open_issues(args, auth)
    quality_gate = _load_quality_gate(args, auth)
    findings: List[str] = []
    ratchet_scoped = getattr(
        args, "policy_mode", "ratchet"
    ) == "ratchet" and _is_scoped_analysis(args)
    if open_issues != 0 and not ratchet_scoped:
        findings.append(f"Sonar reports {open_issues} open issues (expected 0).")
    if quality_gate != "OK" and open_issues != 0:
        findings.append(f"Sonar quality gate status is {quality_gate} (expected OK).")
    return open_issues, quality_gate, findings


def _is_scoped_analysis(args: argparse.Namespace) -> bool:
    """Return whether the Sonar query targets a branch or pull request."""
    return bool(
        _preferred_text(
            getattr(args, "branch", ""),
            getattr(args, "pull_request", ""),
        )
    )


def _target_sha(args: argparse.Namespace) -> str:
    """Return the normalized target SHA used for scoped-analysis settling."""
    return _preferred_text(getattr(args, "sha", "")).lower()


def _find_named_entry(
    items: Iterable[Mapping[str, Any]],
    key: str,
    value: str,
) -> Mapping[str, Any] | None:
    """Return the first mapping whose named field matches the target value."""
    return sonar_zero_support.find_named_entry(
        items,
        key,
        value,
        preferred_text=_preferred_text,
    )


def _analysis_revision_config(
    *,
    analysis_path: str,
    entries_key: str,
    entry_key: str,
    target_value: str,
) -> sonar_zero_support.AnalysisRevisionConfig:
    """Build the Sonar revision lookup configuration for one scope."""
    return sonar_zero_support.AnalysisRevisionConfig(
        request_json=_request_json,
        mapping_or_empty=_mapping_or_empty,
        named_entry=_find_named_entry,
        preferred_text=_preferred_text,
        sonar_api_base=SONAR_API_BASE,
        url_quote=urllib.parse.quote,
        analysis_path=analysis_path,
        entries_key=entries_key,
        entry_key=entry_key,
        target_value=target_value,
    )


def _load_branch_analysis_revision(args: argparse.Namespace, auth: str) -> str:
    """Load the currently indexed commit SHA for one Sonar branch."""
    return sonar_zero_support.load_branch_analysis_revision(
        args,
        auth,
        config=_analysis_revision_config(
            analysis_path="project_branches/list",
            entries_key="branches",
            entry_key="name",
            target_value=_preferred_text(getattr(args, "branch", "")),
        ),
    )


def _load_pull_request_analysis_revision(args: argparse.Namespace, auth: str) -> str:
    """Load the currently indexed commit SHA for one Sonar pull request."""
    return sonar_zero_support.load_pull_request_analysis_revision(
        args,
        auth,
        config=_analysis_revision_config(
            analysis_path="project_pull_requests/list",
            entries_key="pullRequests",
            entry_key="key",
            target_value=_preferred_text(getattr(args, "pull_request", "")),
        ),
    )


def _scoped_analysis_label(args: argparse.Namespace) -> str:
    """Return the current Sonar branch or pull-request scope label."""
    return sonar_zero_support.scoped_analysis_label(args)


def _scoped_analysis_loaders() -> sonar_zero_support.ScopedAnalysisLoaders:
    """Build the loaders for the active Sonar scope."""
    return sonar_zero_support.ScopedAnalysisLoaders(
        load_pull_request_revision=_load_pull_request_analysis_revision,
        load_branch_revision=_load_branch_analysis_revision,
    )


def _load_scoped_analysis_revision(args: argparse.Namespace, auth: str) -> str:
    """Load the current Sonar analysis SHA for the active scope."""
    return sonar_zero_support.load_scoped_analysis_revision(
        args,
        auth,
        loaders=_scoped_analysis_loaders(),
    )


def _scoped_analysis_callbacks() -> sonar_zero_support.ScopedAnalysisCallbacks:
    """Build the callbacks used to evaluate Sonar scope settling."""
    return sonar_zero_support.ScopedAnalysisCallbacks(
        is_scoped_analysis=_is_scoped_analysis,
        target_sha=_target_sha,
        scope_label=_scoped_analysis_label,
        load_revision=_load_scoped_analysis_revision,
    )


def _scoped_analysis_pending_message(args: argparse.Namespace, auth: str) -> str | None:
    """Return the current Sonar pending-analysis message for the active scope."""
    return sonar_zero_support.scoped_analysis_pending_message(
        args,
        auth,
        callbacks=_scoped_analysis_callbacks(),
    )


def _resolve_retry_settings(
    retry_kwargs: Mapping[str, Any],
) -> sonar_zero_support.RetrySettings:
    """Resolve the retry callbacks and timing budget for one Sonar lookup."""
    return sonar_zero_support.resolve_retry_settings(
        retry_kwargs,
        defaults=sonar_zero_support.RetrySettings(
            fetch_fn=_load_sonar_findings,
            pending_fn=_scoped_analysis_pending_message,
            attempts=SCOPED_ANALYSIS_RETRY_ATTEMPTS,
            sleep_seconds=5.0,
        ),
    )


def _retry_exception_result(
    namespace: argparse.Namespace,
    exc: Exception,
    result: Tuple[int, str],
) -> Tuple[int, str, List[str]]:
    """Return the final retry result after one Sonar request exception."""
    return sonar_zero_support.retry_exception_result(
        namespace,
        exc,
        result,
        is_scoped_analysis=_is_scoped_analysis,
    )


def _pending_analysis_message(
    namespace: argparse.Namespace,
    auth: str,
    pending_fn: sonar_zero_support.PendingFn,
) -> str | None:
    """Return a resilient pending-analysis message from the configured callback."""
    return sonar_zero_support.pending_analysis_message(namespace, auth, pending_fn)


def _should_retry_scoped_analysis(
    namespace: argparse.Namespace,
    findings: List[str],
    pending_message: str | None,
) -> bool:
    """Return whether the current Sonar scoped analysis should retry."""
    return sonar_zero_support.should_retry_scoped_analysis(
        namespace,
        findings,
        pending_message,
        is_scoped_analysis=_is_scoped_analysis,
    )


def _final_retry_findings(
    findings: List[str],
    pending_message: str | None,
) -> List[str]:
    """Append the final pending message to the existing Sonar findings."""
    return sonar_zero_support.final_retry_findings(findings, pending_message)


def _retry_handlers() -> sonar_zero_support.RetryHandlers:
    """Build the retry callbacks for the Sonar findings loop."""
    return sonar_zero_support.RetryHandlers(
        retry_exception=_retry_exception_result,
        pending_message_fn=_pending_analysis_message,
        should_retry=_should_retry_scoped_analysis,
        final_findings_fn=_final_retry_findings,
        sleep_fn=time.sleep,
    )


def load_sonar_findings_with_retry(
    *args: Any,
    **kwargs: Any,
) -> Tuple[int, str, List[str]]:
    """Load Sonar findings, retrying while a scoped analysis is still settling."""
    if len(args) != 2:
        raise TypeError(
            "load_sonar_findings_with_retry expects argparse namespace "
            "and auth header"
        )
    namespace, auth = args
    retry_settings = _resolve_retry_settings(kwargs)
    return sonar_zero_support.load_sonar_findings_with_retry(
        namespace,
        auth,
        settings=retry_settings,
        handlers=_retry_handlers(),
    )


def main() -> int:
    """Execute the Sonar zero gate CLI."""
    args = _parse_args()
    token = _preferred_text(args.token, os.environ.get("SONAR_TOKEN", ""))
    findings: List[str] = []
    open_issues: int | None = None
    quality_gate: str | None = None

    if not token:
        findings.append("SONAR_TOKEN is missing.")
        status = "fail"
    else:
        try:
            auth = _auth_header(token)
            (
                open_issues,
                quality_gate,
                findings,
            ) = load_sonar_findings_with_retry(args, auth)
            status = "pass" if not findings else "fail"
            if getattr(args, "policy_mode", "ratchet") == "audit":
                status = "pass"
        except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
            findings.append(f"Sonar API request failed: {exc}")
            status = "fail"

    payload = {
        "status": status,
        "project_key": args.project_key,
        "open_issues": open_issues,
        "quality_gate": quality_gate,
        "timestamp_utc": utc_timestamp(),
        "findings": findings,
    }
    return_code = write_report(
        payload,
        out_json=args.out_json,
        out_md=args.out_md,
        default_json="sonar-zero/sonar.json",
        default_md="sonar-zero/sonar.md",
        render_md=_render_md,
    )
    if return_code != 0:
        return return_code
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
