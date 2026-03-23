#!/usr/bin/env python3
from __future__ import absolute_import

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import safe_output_path, utc_timestamp, write_report
from scripts.security_helpers import load_json_https


GITHUB_API_BASE = "https://api.github.com"
LANE_CONTEXTS = {
    "coverage": "Coverage 100 Gate",
    "qlty_zero": "QLTY Zero",
    "sonar": "Sonar Zero",
    "codacy": "Codacy Zero",
    "sentry": "Sentry Zero",
    "deepscan": "DeepScan Zero",
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
    "deps": "deps-zero/deps.json",
    "secrets": "quality-secrets/secrets.json",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build one aggregated strict-zero rollup.")
    parser.add_argument("--profile-json", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--artifacts-dir", required=True)
    parser.add_argument("--out-json", default="quality-rollup/summary.json")
    parser.add_argument("--out-md", default="quality-rollup/summary.md")
    return parser.parse_args()


def _github_payload(repo: str, sha: str, token: str) -> Dict[str, Any]:
    payload, _ = load_json_https(
        f"{GITHUB_API_BASE}/repos/{repo}/commits/{sha}/check-runs?per_page=100",
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
    payload = _github_payload(repo, sha, token)
    contexts: Dict[str, Dict[str, str]] = {}
    for run in payload.get("check_runs", []) or []:
        name = str(run.get("name") or "").strip()
        if not name:
            continue
        contexts[name] = {
            "state": str(run.get("status") or ""),
            "conclusion": str(run.get("conclusion") or ""),
            "source": "check_run",
        }
    return contexts


def load_lane_payloads(artifacts_root: Path) -> Dict[str, Dict[str, Any]]:
    payloads: Dict[str, Dict[str, Any]] = {}
    for lane, relative_path in LANE_ARTIFACT_PATHS.items():
        artifact_dir = artifacts_root / f"{lane}-artifacts"
        json_path = artifact_dir / relative_path
        if not json_path.is_file():
            continue
        payloads[lane] = json.loads(json_path.read_text(encoding="utf-8"))
    return payloads


def _lane_detail(payload: Mapping[str, Any]) -> str:
    findings = payload.get("findings", [])
    if isinstance(findings, list) and findings:
        return str(findings[0])
    if "open_issues" in payload and payload.get("open_issues") is not None:
        return f"Open issues: {payload['open_issues']}"
    if "quality_gate" in payload and payload.get("quality_gate"):
        return f"Quality gate: {payload['quality_gate']}"
    if "mode" in payload and payload.get("mode"):
        return f"Mode: {payload['mode']}"
    return "No findings."


def _status_from_context(context_name: str, contexts: Mapping[str, Dict[str, str]]) -> str:
    details = contexts.get(context_name)
    if not details:
        return "missing"
    if details.get("state") != "completed":
        return "pending"
    return "pass" if details.get("conclusion") == "success" else "fail"


def build_rollup(
    *,
    profile: Mapping[str, Any],
    lane_payloads: Mapping[str, Dict[str, Any]],
    contexts: Mapping[str, Dict[str, str]],
    sha: str,
) -> Dict[str, Any]:
    required_contexts = sorted(profile.get("active_required_contexts", []))
    rows: List[Dict[str, str]] = []
    overall = "pass"
    reverse_map = {context: lane for lane, context in LANE_CONTEXTS.items()}
    for context_name in required_contexts:
        lane = reverse_map.get(context_name)
        lane_payload = lane_payloads.get(lane or "")
        status = "pass" if lane_payload and lane_payload.get("status") == "pass" else _status_from_context(context_name, contexts)
        if status in {"fail", "missing"}:
            overall = "fail"
        detail = _lane_detail(lane_payload) if lane_payload else "No findings."
        rows.append(
            {
                "context": context_name,
                "status": status,
                "detail": detail,
            }
        )
    return {
        "repo": profile["slug"],
        "sha": sha,
        "status": overall,
        "generated_at": utc_timestamp(),
        "contexts": rows,
    }


def render_markdown(payload: Mapping[str, Any]) -> str:
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
    args = parse_args()
    profile = json.loads(Path(args.profile_json).read_text(encoding="utf-8"))
    token = (os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")).strip()
    contexts = load_check_contexts(args.repo, args.sha, token) if token else {}
    lane_payloads = load_lane_payloads(Path(args.artifacts_dir))
    payload = build_rollup(profile=profile, lane_payloads=lane_payloads, contexts=contexts, sha=args.sha)
    return write_report(
        payload,
        out_json=str(safe_output_path(args.out_json, "quality-rollup/summary.json")),
        out_md=str(safe_output_path(args.out_md, "quality-rollup/summary.md")),
        default_json="quality-rollup/summary.json",
        default_md="quality-rollup/summary.md",
        render_md=render_markdown,
    )


if __name__ == "__main__":
    raise SystemExit(main())
