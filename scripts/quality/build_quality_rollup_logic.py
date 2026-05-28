#!/usr/bin/env python3
"""Pure rollup-building logic split from ``build_quality_rollup``.

Houses the per-lane status resolution, row building, severity aggregation, and
markdown rendering so the CLI driver (GitHub polling + report writing) stays
bounded in file-level complexity. The public names are re-exported from
``build_quality_rollup`` to preserve the historical import surface.
"""

from __future__ import absolute_import

from typing import Any, Dict, List, Mapping

from scripts.quality.check_required_checks import (
    _evaluate_observed_context,
    _resolve_observed_context,
)
from scripts.quality.common import utc_timestamp
from scripts.quality.severity_rollup import (
    classify_lanes,
    failing_lanes_to_gate_output,
)

LANE_CONTEXTS = {
    "coverage": "Coverage 100 Gate",
    "qlty_zero": "QLTY Zero",
    "sonar": "Sonar Zero",
    "codacy": "Codacy Zero",
    "sentry": "Sentry Zero",
    "deepscan": "DeepScan Zero",
    "deepsource_visible": "DeepSource Visible Zero",
    "deps": "Dependency Alerts",
    "secrets": "Quality Secrets Preflight",
}
LANE_ARTIFACT_PATHS = {
    "coverage": "coverage-100/coverage.json",
    "qlty_zero": "qlty-zero/qlty-zero.json",
    "sonar": "sonar-zero/sonar.json",
    "codacy": "codacy-zero/codacy.json",
    "sentry": "sentry-zero/sentry.json",
    "deepscan": "deepscan-zero/deepscan.json",
    "deepsource_visible": "deepsource-visible-zero/deepsource.json",
    "deps": "deps-zero/deps.json",
    "secrets": "quality-secrets/secrets.json",
}


def _lane_detail(payload: Mapping[str, Any]) -> str:
    """Summarize the first meaningful detail from one lane payload."""
    findings = payload.get("findings", [])
    if isinstance(findings, list) and findings:
        return str(findings[0])
    for key, template in (
        ("open_issues", "Open issues: {}"),
        ("quality_gate", "Quality gate: {}"),
        ("mode", "Mode: {}"),
    ):
        value = payload.get(key)
        if value is not None and value != "":
            return template.format(value)
    return "No findings."


def _status_from_context(
    context_name: str,
    contexts: Mapping[str, Dict[str, str]],
) -> str:
    """Resolve a normalized pass, fail, pending, or missing status."""
    details = _resolve_observed_context(context_name, contexts)
    if not details:
        return "missing"
    if details.get("source") == "check_run" and details.get("state") != "completed":
        return "pending"
    if details.get("source") == "status" and details.get("conclusion") == "pending":
        return "pending"
    failure = _evaluate_observed_context(context_name, details)
    return "fail" if failure else "pass"


def _build_rollup_row(
    *,
    context_name: str,
    reverse_map: Mapping[str, str],
    lane_payloads: Mapping[str, Dict[str, Any]],
    contexts: Mapping[str, Dict[str, str]],
) -> Dict[str, str]:
    """Build one row for the markdown and JSON rollup outputs."""
    lane = reverse_map.get(context_name)
    lane_payload = lane_payloads.get(lane or "")
    status = (
        "pass"
        if lane_payload and lane_payload.get("status") == "pass"
        else _status_from_context(context_name, contexts)
    )
    detail = _lane_detail(lane_payload) if lane_payload else "No findings."
    return {
        "context": context_name,
        "status": status,
        "detail": detail,
    }


def _lane_statuses_from_rows(
    rows: List[Dict[str, str]],
    reverse_map: Mapping[str, str],
) -> Dict[str, str]:
    """Return ``{lane_id: status}`` keyed off ``reverse_map`` for severity rollup.

    Contexts that can't be mapped to a lane (e.g. ``SonarCloud Code
    Analysis`` isn't in ``LANE_CONTEXTS``) pass through under their
    context name so the severity map can still match by that key.
    """
    statuses: Dict[str, str] = {}
    for row in rows:
        context_name = row["context"]
        lane_key = reverse_map.get(context_name, context_name)
        statuses[lane_key] = row["status"]
    return statuses


def _aggregate_rollup_status(rows: List[Dict[str, str]]) -> str:
    """Reduce per-row statuses to a single overall verdict.

    Hard-fails win; otherwise pending wins; otherwise pass.
    """
    overall = "pass"
    for row in rows:
        status = row["status"]
        if status in {"fail", "missing"}:
            return "fail"
        if status == "pending" and overall == "pass":
            overall = "pending"
    return overall


def _apply_severity_softening(overall: str, severity_verdict: str) -> str:
    """If overall is ``fail`` but severity verdict is warn/pass, soften it.

    Preserves the hard ``fail`` when blockers exist AND preserves
    ``pending`` (not-yet-reported) regardless.
    """
    if overall != "fail":
        return overall
    if severity_verdict == "warn":
        return "warn"
    if severity_verdict == "pass":
        return "pass"
    return overall


def build_rollup(
    *,
    profile: Mapping[str, Any],
    lane_payloads: Mapping[str, Dict[str, Any]],
    contexts: Mapping[str, Dict[str, str]],
    sha: str,
) -> Dict[str, Any]:
    """Build the aggregated strict-zero rollup payload.

    v2 profiles declare ``scanners.*.severity`` (block/warn/info);
    :func:`scripts.quality.severity_rollup.classify_lanes` downgrades
    ``warn``/``info`` lane failures from the overall verdict while
    still tracking them in the ``severity`` sub-payload. Legacy v1
    profiles that don't declare ``scanners`` default every lane to
    ``block`` severity (same as the existing behaviour).
    """
    required_contexts = sorted(profile.get("active_required_contexts", []))
    reverse_map = {context: lane for lane, context in LANE_CONTEXTS.items()}
    rows = [
        _build_rollup_row(
            context_name=context_name,
            reverse_map=reverse_map,
            lane_payloads=lane_payloads,
            contexts=contexts,
        )
        for context_name in required_contexts
    ]
    overall = _aggregate_rollup_status(rows)
    severity_verdict = classify_lanes(
        profile, _lane_statuses_from_rows(rows, reverse_map)
    )
    severity_payload = failing_lanes_to_gate_output(severity_verdict)
    overall = _apply_severity_softening(overall, severity_verdict.verdict)
    return {
        "repo": profile["slug"],
        "sha": sha,
        "status": overall,
        "generated_at": utc_timestamp(),
        "contexts": rows,
        "severity": severity_payload,
    }


def render_markdown(payload: Mapping[str, Any]) -> str:
    """Render the aggregated quality rollup as a markdown table."""
    lines = [
        "# Quality Rollup",
        "",
        f"- Repo: `{payload['repo']}`",
        f"- SHA: `{payload['sha']}`",
        f"- Status: `{payload['status']}`",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "",
        "| Context | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for item in payload.get("contexts", []):
        lines.append(f"| `{item['context']}` | `{item['status']}` | {item['detail']} |")
    return "\n".join(lines) + "\n"
