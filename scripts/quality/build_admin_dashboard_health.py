#!/usr/bin/env python3
"""Live GitHub health probes for the admin dashboard.

Split from :mod:`scripts.quality.build_admin_dashboard` so the GitHub API
fetching + workflow-run health computation lives in a cohesive module, keeping
each module's file-level complexity bounded. The public names are re-exported
from ``build_admin_dashboard`` to preserve the historical import surface.
"""

from __future__ import absolute_import

from typing import Any, Dict, List, Mapping, Sequence, Set

from scripts.security_helpers import load_json_https

GITHUB_API_BASE = "https://api.github.com"


def _github_payload(url: str, token: str) -> Any:
    """Handle github payload."""
    payload, _ = load_json_https(
        url,
        allowed_hosts={"api.github.com"},
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "quality-zero-platform",
        },
    )
    return payload


def _select_runs(
    workflow_runs: Sequence[Mapping[str, Any]], *, filter_fn=None
) -> List[Mapping[str, Any]]:
    """Handle select runs."""
    if filter_fn is None:
        return list(workflow_runs)
    return [item for item in workflow_runs if filter_fn(item)]


def _run_conclusions(workflow_runs: Sequence[Mapping[str, Any]]) -> Set[str]:
    """Handle run conclusions."""
    return {
        str(item.get("conclusion") or "")
        for item in workflow_runs
        if item.get("conclusion")
    }


def _compute_health(
    workflow_runs: Sequence[Mapping[str, Any]], *, filter_fn=None
) -> str:
    """Handle compute health."""
    runs = _select_runs(workflow_runs, filter_fn=filter_fn)
    if not runs:
        return "unknown"
    conclusions = _run_conclusions(runs)
    return "success" if conclusions and conclusions <= {"success"} else "partial"


def _live_health(token: str, repo_slug: str, default_branch: str) -> Dict[str, Any]:
    """Handle live health."""
    runs = _github_payload(
        (
            f"{GITHUB_API_BASE}/repos/{repo_slug}/actions/runs"
            f"?branch={default_branch}&per_page=20"
        ),
        token,
    )
    rulesets = _github_payload(f"{GITHUB_API_BASE}/repos/{repo_slug}/rulesets", token)
    # Fetch repo metadata to capture visibility for the redaction step
    # in ``build_dashboard_payload``. Using the same token lets private
    # repos (where the bot has read access) report their true visibility;
    # repos visible only as public report ``"public"``. Defaults to
    # ``"public"`` on any failure so a missing/erroring metadata fetch
    # cannot accidentally redact (or leak via mis-default).
    repo_meta = _github_payload(f"{GITHUB_API_BASE}/repos/{repo_slug}", token)
    visibility = "public"
    if isinstance(repo_meta, dict):
        raw_visibility = repo_meta.get("visibility")
        if isinstance(raw_visibility, str) and raw_visibility.strip():
            visibility = raw_visibility.strip().lower()
    workflow_runs = runs.get("workflow_runs", []) if isinstance(runs, dict) else []
    return {
        "default_branch_health": _compute_health(workflow_runs),
        "open_pr_health": _compute_health(
            workflow_runs,
            filter_fn=lambda item: item.get("event") == "pull_request",
        ),
        "ruleset_present": bool(rulesets),
        "visibility": visibility,
    }
