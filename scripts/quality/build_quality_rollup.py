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
from typing import Any, Dict, List

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.build_quality_rollup_logic import (  # noqa: F401  pylint: disable=unused-import
    LANE_ARTIFACT_PATHS,
    LANE_CONTEXTS,
    _aggregate_rollup_status,
    _apply_severity_softening,
    _build_rollup_row,
    _lane_detail,
    _lane_statuses_from_rows,
    _status_from_context,
    build_rollup,
    render_markdown,
)
from scripts.quality.check_required_checks import _collect_contexts
from scripts.quality.common import write_report
from scripts.security_helpers import load_json_https

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_ROLLUP_JSON = "quality-rollup/summary.json"
DEFAULT_ROLLUP_MD = "quality-rollup/summary.md"


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
