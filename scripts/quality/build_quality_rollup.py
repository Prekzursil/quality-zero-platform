#!/usr/bin/env python3
"""Build one markdown and JSON summary for the governed quality lanes.

# TODO(qrv2-pr4): remove this legacy wrapper after all downstream consumers
# are migrated to scripts.quality.rollup_v2.pipeline.run_pipeline().
# This module is kept for backward compatibility during the transition.
# The canonical rollup_v2 pipeline is at scripts/quality/rollup_v2/pipeline.py.
"""

from __future__ import absolute_import

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.check_required_checks import (
    _collect_contexts,
    _evaluate_observed_context,
    _resolve_observed_context,
)
from scripts.quality.common import utc_timestamp, write_report
from scripts.quality.severity_rollup import (
    classify_lanes,
    failing_lanes_to_gate_output,
)
from scripts.security_helpers import load_json_https

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_ROLLUP_JSON = "quality-rollup/summary.json"
DEFAULT_ROLLUP_MD = "quality-rollup/summary.md"
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


@dataclass(frozen=True)
class ContextWaitRequest:
    """Describe the status contexts that must settle before rollup."""

    repo: str
    sha: str
    token: str
    required_contexts: List[str]
    timeout_seconds: int = 900
    poll_seconds: int = 20


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the quality rollup builder."""
    parser = argparse.ArgumentParser(
        description="Build one aggregated strict-zero rollup."
    )
    parser.add_argument("--profile-json", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--artifacts-dir", required=True)
    parser.add_argument("--out-json", default=DEFAULT_ROLLUP_JSON)
    parser.add_argument("--out-md", default=DEFAULT_ROLLUP_MD)
    return parser.parse_args()


def _github_payload(repo: str, path: str, token: str) -> Dict[str, Any]:
    """Fetch one GitHub API payload used by the rollup script."""
    payload, _ = load_json_https(
        f"{GITHUB_API_BASE}/repos/{repo}/commits/{path}",
        allowed_hosts={"api.github.com"},
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "quality-zero-platform",
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected GitHub API response payload")
    return payload


def load_check_contexts(repo: str, sha: str, token: str) -> Dict[str, Dict[str, str]]:
    """Load both check-run and status contexts for the target SHA."""
    check_runs = _github_payload(repo, f"{sha}/check-runs?per_page=100", token)
    statuses = _github_payload(repo, f"{sha}/status", token)
    return _collect_contexts(check_runs, statuses)


def load_lane_payloads(artifacts_root: Path) -> Dict[str, Dict[str, Any]]:
    """Load per-lane artifacts that were published by scanner jobs."""
    payloads: Dict[str, Dict[str, Any]] = {}
    for lane, relative_path in LANE_ARTIFACT_PATHS.items():
        artifact_dir = artifacts_root / f"{lane}-artifacts"
        json_path = artifact_dir / relative_path
        if not json_path.is_file():
            continue
        payloads[lane] = json.loads(json_path.read_text(encoding="utf-8"))
    return payloads


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


def _wait_for_contexts(request: ContextWaitRequest) -> Dict[str, Dict[str, str]]:
    """Poll GitHub until the required contexts stop being pending or missing."""
    deadline = time.time() + max(request.timeout_seconds, 1)
    final_contexts: Dict[str, Dict[str, str]] = {}
    while time.time() <= deadline:
        final_contexts = load_check_contexts(request.repo, request.sha, request.token)
        statuses = [
            _status_from_context(context_name, final_contexts)
            for context_name in request.required_contexts
        ]
        if "pending" not in statuses and "missing" not in statuses:
            break
        time.sleep(max(request.poll_seconds, 0))
    return final_contexts


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


def main() -> int:
    """Run the quality rollup generator."""
    args = parse_args()
    profile = json.loads(Path(args.profile_json).read_text(encoding="utf-8"))
    token = (
        os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")
    ).strip()
    required_contexts = sorted(profile.get("active_required_contexts", []))
    contexts = (
        _wait_for_contexts(
            ContextWaitRequest(
                repo=args.repo,
                sha=args.sha,
                token=token,
                required_contexts=required_contexts,
            )
        )
        if token
        else {}
    )
    lane_payloads = load_lane_payloads(Path(args.artifacts_dir))
    payload = build_rollup(
        profile=profile,
        lane_payloads=lane_payloads,
        contexts=contexts,
        sha=args.sha,
    )
    return_code = write_report(
        payload,
        out_json=args.out_json,
        out_md=args.out_md,
        default_json=DEFAULT_ROLLUP_JSON,
        default_md=DEFAULT_ROLLUP_MD,
        render_md=render_markdown,
    )
    if return_code != 0:
        return return_code
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
