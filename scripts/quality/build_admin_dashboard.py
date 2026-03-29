#!/usr/bin/env python3
"""Build admin dashboard."""

from __future__ import absolute_import

import argparse
import shutil
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Set

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp
from scripts.quality.control_plane import load_inventory, load_repo_profile
from scripts.security_helpers import load_json_https

GITHUB_API_BASE = "https://api.github.com"


def parse_args() -> argparse.Namespace:
    """Handle parse args."""
    parser = argparse.ArgumentParser(
        description="Build the GitHub Pages admin dashboard payload."
    )
    parser.add_argument("--inventory", default="")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--assets-dir", default="")
    return parser.parse_args()


def build_dashboard_payload(
    *,
    inventory: Mapping[str, Any],
    profiles: Mapping[str, Dict[str, Any]],
    live: Mapping[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Return build dashboard payload."""
    repos: List[Dict[str, Any]] = []
    for repo_entry in inventory.get("repos", []):
        slug = repo_entry["slug"]
        profile = profiles[slug]
        live_state = live.get(slug, {})
        repos.append(
            {
                "slug": slug,
                "profile": repo_entry.get("profile", ""),
                "rollout": repo_entry.get("rollout", ""),
                "issue_policy_mode": profile.get("issue_policy", {}).get("mode", ""),
                "issue_policy_baseline_ref": profile.get("issue_policy", {}).get(
                    "baseline_ref", ""
                ),
                "enabled_scanners": sorted(
                    name
                    for name, enabled in profile.get("enabled_scanners", {}).items()
                    if enabled
                ),
                "coverage_min_percent": profile.get("coverage", {}).get("min_percent"),
                "branch_min_percent": profile.get("coverage", {}).get(
                    "branch_min_percent"
                ),
                "deps_policy": profile.get("deps", {}).get("policy", ""),
                "default_branch_health": live_state.get(
                    "default_branch_health", "unknown"
                ),
                "open_pr_health": live_state.get("open_pr_health", "unknown"),
                "ruleset_present": bool(live_state.get("ruleset_present", False)),
            }
        )
    return {
        "generated_at": utc_timestamp(),
        "repo_count": len(repos),
        "repos": repos,
    }


def _baseline_text(item: Mapping[str, Any]) -> str:
    """Handle baseline text."""
    baseline_ref = str(item.get("issue_policy_baseline_ref", "") or "").strip()
    return f" ({baseline_ref})" if baseline_ref else ""


def _render_repo_row(item: Mapping[str, Any]) -> str:
    """Handle render repo row."""
    return "\n".join(
        [
            "<tr>",
            f"<td>{item['slug']}</td>",
            f"<td>{item['profile']}</td>",
            f"<td>{item['rollout']}</td>",
            f"<td>{item['issue_policy_mode']}{_baseline_text(item)}</td>",
            f"<td>{', '.join(item['enabled_scanners'])}</td>",
            f"<td>{item.get('branch_min_percent')}</td>",
            f"<td>{item.get('deps_policy')}</td>",
            f"<td>{item['default_branch_health']}</td>",
            f"<td>{item['open_pr_health']}</td>",
            f"<td>{'yes' if item['ruleset_present'] else 'no'}</td>",
            "</tr>",
        ]
    )


def _render_dashboard_page(payload: Mapping[str, Any], rows: str) -> str:
    """Handle render dashboard page."""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Quality Zero Control Plane</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 2rem; background: #f7f7f7; color: #111; }}
    table {{ border-collapse: collapse; width: 100%; background: white; }}
    th, td {{ border: 1px solid #ddd; padding: 0.5rem; text-align: left; vertical-align: top; }}
    th {{ background: #eee; }}
    .meta {{ margin-bottom: 1rem; }}
  </style>
</head>
<body>
  <h1>Quality Zero Control Plane</h1>
  <p class="meta">Generated at {payload.get('generated_at', '')}. Governed repos: {payload.get('repo_count', 0)}.</p>
  <table>
    <thead>
      <tr>
        <th>Repo</th>
        <th>Profile</th>
        <th>Rollout</th>
        <th>Issue policy</th>
        <th>Enabled scanners</th>
        <th>Branch coverage</th>
        <th>Deps policy</th>
        <th>Default branch</th>
        <th>Open PRs</th>
        <th>Ruleset</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</body>
</html>
"""


def render_dashboard_html(payload: Mapping[str, Any]) -> str:
    """Handle render dashboard html."""
    rows = "\n".join(_render_repo_row(item) for item in payload.get("repos", []))
    return _render_dashboard_page(payload, rows)


def write_dashboard(
    output_dir: Path, payload: Mapping[str, Any], *, assets_dir: Path | None = None
) -> None:
    """Handle write dashboard."""
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "dashboard.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if assets_dir is not None:
        for asset_name in ("index.html", "styles.css", "app.js"):
            shutil.copy2(assets_dir / asset_name, output_dir / asset_name)
        return
    (output_dir / "index.html").write_text(
        render_dashboard_html(payload), encoding="utf-8"
    )


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
    workflow_runs: List[Mapping[str, Any]], *, filter_fn=None
) -> List[Mapping[str, Any]]:
    """Handle select runs."""
    if filter_fn is None:
        return workflow_runs
    return [item for item in workflow_runs if filter_fn(item)]


def _run_conclusions(workflow_runs: List[Mapping[str, Any]]) -> Set[str]:
    """Handle run conclusions."""
    return {
        str(item.get("conclusion") or "")
        for item in workflow_runs
        if item.get("conclusion")
    }


def _compute_health(workflow_runs: List[Mapping[str, Any]], *, filter_fn=None) -> str:
    """Handle compute health."""
    runs = _select_runs(workflow_runs, filter_fn=filter_fn)
    if not runs:
        return "unknown"
    conclusions = _run_conclusions(runs)
    return "success" if conclusions and conclusions <= {"success"} else "partial"


def _live_health(token: str, repo_slug: str, default_branch: str) -> Dict[str, Any]:
    """Handle live health."""
    runs = _github_payload(
        f"{GITHUB_API_BASE}/repos/{repo_slug}/actions/runs?branch={default_branch}&per_page=20",
        token,
    )
    rulesets = _github_payload(f"{GITHUB_API_BASE}/repos/{repo_slug}/rulesets", token)
    workflow_runs = runs.get("workflow_runs", []) if isinstance(runs, dict) else []
    return {
        "default_branch_health": _compute_health(workflow_runs),
        "open_pr_health": _compute_health(
            workflow_runs,
            filter_fn=lambda item: item.get("event") == "pull_request",
        ),
        "ruleset_present": bool(rulesets),
    }


def main() -> int:
    """Handle main."""
    args = parse_args()
    inventory = load_inventory(args.inventory) if args.inventory else load_inventory()
    profiles = {
        repo_entry["slug"]: load_repo_profile(inventory, repo_entry["slug"])
        for repo_entry in inventory["repos"]
    }
    token = (
        os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")
    ).strip()
    live = {}
    if token:
        for repo_entry in inventory["repos"]:
            live[repo_entry["slug"]] = _live_health(
                token, repo_entry["slug"], repo_entry.get("default_branch", "main")
            )
    payload = build_dashboard_payload(inventory=inventory, profiles=profiles, live=live)
    docs_admin = (
        Path(args.assets_dir).resolve()
        if args.assets_dir
        else Path(__file__).resolve().parents[2] / "docs" / "admin"
    )
    assets_dir = docs_admin if docs_admin.exists() else None
    write_dashboard(Path(args.output_dir), payload, assets_dir=assets_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
