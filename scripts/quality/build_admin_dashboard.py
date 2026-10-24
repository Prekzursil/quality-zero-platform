#!/usr/bin/env python3
"""Build admin dashboard."""

from __future__ import absolute_import

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple
from urllib.error import HTTPError

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.admin_dashboard_pages import (
    redact_private_repos,
)
from scripts.quality.check_required_checks import (
    _collect_contexts,
    _evaluate_observed_context,
    _resolve_observed_context,
)
from scripts.quality.common import utc_timestamp
from scripts.quality.control_plane import load_inventory, load_repo_profile
from scripts.quality.validate_required_contexts import PR_ONLY_CONTEXTS, collect_required
from scripts.security_helpers import load_json_https

GITHUB_API_BASE = "https://api.github.com"
# Check-runs pagination cap: 5 pages × 100 runs. A re-run-heavy commit can
# exceed one page (tracelines HEAD measured at 120 runs) but a 500-run walk
# is far beyond any real required-context set — the cap stops a pathological
# repo from stalling the hourly build.
MAX_CHECK_RUN_PAGES = 5
# One pull_request-run lookup per open PR head, capped so a repo with a huge
# PR backlog cannot stall the hourly build (heads past the cap: no verdict).
MAX_PR_HEAD_LOOKUPS = 20
#: The default-branch verdict reads this many of the repository's own newest
#: runs: 100, not 20, so a weekly or schedule-only workflow still has a run in
#: the window instead of silently dropping out of the verdict.
DEFAULT_BRANCH_WINDOW = 100
#: Pages read to fill that window once PR-triggered runs are filtered out.
MAX_DEFAULT_BRANCH_PAGES = 3
#: Conclusions that neither pass nor fail a workflow (the platform's own
#: failing-workflow alert uses the same set): a run that concurrency
#: cancelled, or a skipped job, says nothing about health.
NEUTRAL_CONCLUSIONS = frozenset({"cancelled", "skipped", "neutral", "action_required", "stale"})
#: Events a pull request triggers. ``actions/runs?branch=<default>`` matches a
#: run's HEAD branch, so a fork PR opened from the fork's own ``main``, and a
#: ``pull_request_target`` run (which carries the base branch), both land in
#: the default-branch window while they test pull-request code.
PR_EVENTS = frozenset({"pull_request", "pull_request_target", "pull_request_review", "pull_request_review_comment"})


def parse_args() -> argparse.Namespace:
    """Handle parse args."""
    parser = argparse.ArgumentParser(description="Build the GitHub Pages admin dashboard payload.")
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
                # ``visibility`` rides the payload through ``redact_private_repos``
                # below so the public heatmap (deployed to GitHub Pages) never
                # exposes private-repo slugs. Defaults to "public" so missing
                # live state can't accidentally mark a repo private — but
                # also can't accidentally leak. Live state seeded from
                # GitHub API in ``_live_health``.
                "visibility": str(live_state.get("visibility", "public")).lower(),
                "profile": repo_entry.get("profile", ""),
                "rollout": repo_entry.get("rollout", ""),
                "issue_policy_mode": profile.get("issue_policy", {}).get("mode", ""),
                "issue_policy_baseline_ref": profile.get("issue_policy", {}).get("baseline_ref", ""),
                "enabled_scanners": sorted(
                    name for name, enabled in profile.get("enabled_scanners", {}).items() if enabled
                ),
                "coverage_min_percent": profile.get("coverage", {}).get("min_percent"),
                "branch_min_percent": profile.get("coverage", {}).get("branch_min_percent"),
                "deps_policy": profile.get("deps", {}).get("policy", ""),
                "default_branch_health": live_state.get("default_branch_health", "unknown"),
                "open_pr_health": live_state.get("open_pr_health", "unknown"),
                "ruleset_present": bool(live_state.get("ruleset_present", False)),
                # Lane-8 gate panel: per-required-context verdicts measured on
                # the default-branch HEAD (``_lane_gates``).
                "gate_status": live_state.get("gate_status", "unknown"),
                "gates": list(live_state.get("gates", [])),
                "gate_head_sha": live_state.get("gate_head_sha", ""),
                "gate_head_date": live_state.get("gate_head_date", ""),
                "gate_note": live_state.get("gate_note", ""),
            }
        )
    # Redact private slugs BEFORE the payload reaches any rendering or
    # serialisation path. Phase 5 §8 requires that private-repo rows on
    # the public dashboard show only the placeholder, never the real slug.
    redacted = redact_private_repos(repos)
    return {
        "generated_at": utc_timestamp(),
        "repo_count": len(redacted),
        "repos": redacted,
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
    body {{
      font-family: Arial, sans-serif; margin: 2rem; background: #f7f7f7; color: #111;
    }}
    table {{ border-collapse: collapse; width: 100%; background: white; }}
    th, td {{
      border: 1px solid #ddd; padding: 0.5rem; text-align: left; vertical-align: top;
    }}
    th {{ background: #eee; }}
    .meta {{ margin-bottom: 1rem; }}
  </style>
</head>
<body>
  <h1>Quality Zero Control Plane</h1>
  <p class="meta">
    Generated at {payload.get("generated_at", "")}. Governed repos:
    {payload.get("repo_count", 0)}.
  </p>
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


def write_dashboard(output_dir: Path, payload: Mapping[str, Any], *, assets_dir: Path | None = None) -> None:
    """Handle write dashboard."""
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "dashboard.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    if assets_dir is not None:
        # ``panels.mjs`` (Lane 8 operator panels) is imported by ``app.js``
        # at runtime — it must ride the static asset copy or the published
        # SPA 404s the module import.
        for asset_name in ("index.html", "styles.css", "app.js", "panels.mjs"):
            shutil.copy2(assets_dir / asset_name, output_dir / asset_name)
        return
    (output_dir / "index.html").write_text(render_dashboard_html(payload), encoding="utf-8", newline="\n")


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


def _github_payload_or_none(url: str, token: str) -> Any:
    """Fetch ``url``; return ``None`` when GitHub reports the resource 404.

    A newly-registered governed repo can be PRIVATE and/or lack the fetched
    resource entirely (no Pages site, no rulesets, or a repo the workflow
    token cannot read at all) — GitHub reports every one of those as HTTP
    404. That is a per-repo "resource is absent" signal, not a build-stopping
    error: the caller degrades that repo's row (health ``unknown``, redacted
    slug) instead of aborting the whole dashboard publish, mirroring how
    ``redact_private_repos`` already tolerates private repos. Any other
    ``HTTPError`` (403 rate-limit, 5xx, ...) still propagates. The SSRF
    hardening in ``scripts.security_helpers`` keeps raising unconditionally;
    only this caller decides a 404 is tolerable.
    """
    try:
        return _github_payload(url, token)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _github_fetch_degraded(url: str, token: str) -> Tuple[Any, Mapping[str, str] | None]:
    """Fetch ``url``; ``(payload, headers)`` or ``(None, None)`` on 404/403.

    Broader than ``_github_payload_or_none`` (the heatmap's 404-only
    contract above, kept unchanged): the Lane-8 gate panel must also survive
    403, because a token CAN read a private member's metadata while being
    denied its check-runs feed (measured live 2026-10-21 against
    Prekzursil/PrimariRo: head commit 200, check-runs 403), and an hourly
    dashboard publish must degrade that ONE repo to a stale row instead of
    failing the whole build. Any other ``HTTPError`` (5xx, ...) still
    propagates. Headers ride along so callers can follow the Link header.
    """
    try:
        payload, headers = load_json_https(
            url,
            allowed_hosts={"api.github.com"},
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "quality-zero-platform",
            },
        )
    except HTTPError as exc:
        if exc.code in (403, 404):
            return None, None
        raise
    return payload, headers


def _github_payload_or_degraded(url: str, token: str) -> Any:
    """Payload-only view of :func:`_github_fetch_degraded` (Lane 8)."""
    payload, _headers = _github_fetch_degraded(url, token)
    return payload


def _select_runs(workflow_runs: Sequence[Mapping[str, Any]], *, filter_fn=None) -> List[Mapping[str, Any]]:
    """Handle select runs."""
    if filter_fn is None:
        return list(workflow_runs)
    return [item for item in workflow_runs if filter_fn(item)]


def _compute_health(workflow_runs: Sequence[Mapping[str, Any]], *, filter_fn=None) -> str:
    """One verdict from the NEWEST decisive run of each workflow.

    ``partial`` when some workflow's newest completed, non-neutral run did not
    succeed; ``pending`` when a workflow has nothing but runs still in flight;
    ``success`` when every decided workflow passed; ``unknown`` when no run
    says anything. A failure that a later run fixed, or a run that concurrency
    cancelled, no longer paints a green branch red.
    """
    runs = sorted(
        _select_runs(workflow_runs, filter_fn=filter_fn),
        key=lambda run: str(run.get("created_at", "")),
        reverse=True,
    )
    decided: Dict[str, str] = {}
    in_flight: Set[str] = set()
    for run in runs:
        key = str(run.get("workflow_id") or run.get("name") or "")
        conclusion = str(run.get("conclusion") or "")
        if key in decided or conclusion in NEUTRAL_CONCLUSIONS:
            continue
        if run.get("status", "completed") != "completed" or not conclusion:
            in_flight.add(key)
            continue
        decided[key] = conclusion
    verdicts = set(decided.values())
    if verdicts - {"success"}:
        return "partial"
    if in_flight - decided.keys():
        return "pending"
    return "success" if verdicts else "unknown"


def _head_sha(pull: Any) -> str:
    """The head commit of one ``GET /pulls`` record ("" if malformed)."""
    head = pull.get("head") if isinstance(pull, Mapping) else None
    return str(head.get("sha") or "") if isinstance(head, Mapping) else ""


def _workflow_runs_of(payload: Any) -> List[Mapping[str, Any]]:
    """The ``workflow_runs`` list of an ``actions/runs`` response ([] when unusable)."""
    runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
    return [run for run in runs if isinstance(run, Mapping)]


def _head_runs(token: str, repo_slug: str, sha: str) -> Tuple[List[Mapping[str, Any]], bool]:
    """``(pull_request runs on a head, complete)``; ``complete`` is False when they span more than one page."""
    payload, headers = _github_fetch_degraded(
        f"{GITHUB_API_BASE}/repos/{repo_slug}/actions/runs?head_sha={sha}&event=pull_request&per_page=100", token
    )
    return _workflow_runs_of(payload), not _next_page_url(headers or {})


def _is_default_branch_run(run: Mapping[str, Any], repo_slug: str) -> bool:
    """A run of this repository's own default-branch code, not one a pull request or a fork triggered."""
    head_repo = run.get("head_repository")
    name = head_repo.get("full_name") if isinstance(head_repo, Mapping) else None
    foreign = isinstance(name, str) and name.lower() != repo_slug.lower()
    return run.get("event") not in PR_EVENTS and not foreign


def _default_branch_health(token: str, repo_slug: str, default_branch: str) -> str:
    """The verdict over the newest ``DEFAULT_BRANCH_WINDOW`` runs of this repository's own default-branch code.

    The branch filter matches HEAD branches, so PR-triggered and fork runs
    share the listing; they are dropped, and further pages are read until the
    window is full or the listing ends. If the page cap stops that first, a
    failure already visible is still reported; anything else is unproven.
    """
    eligible: List[Mapping[str, Any]] = []
    exhausted = False
    for page in range(1, MAX_DEFAULT_BRANCH_PAGES + 1):
        payload = _github_payload_or_none(
            f"{GITHUB_API_BASE}/repos/{repo_slug}/actions/runs"
            f"?branch={default_branch}&per_page={DEFAULT_BRANCH_WINDOW}&page={page}",
            token,
        )
        runs = _workflow_runs_of(payload)
        eligible.extend(run for run in runs if _is_default_branch_run(run, repo_slug))
        if len(runs) < DEFAULT_BRANCH_WINDOW:
            exhausted = True
            break
        if len(eligible) >= DEFAULT_BRANCH_WINDOW:
            break
    newest = sorted(eligible, key=lambda run: str(run.get("created_at", "")), reverse=True)
    verdict = _compute_health(newest[:DEFAULT_BRANCH_WINDOW])
    if verdict != "partial" and not exhausted and len(eligible) < DEFAULT_BRANCH_WINDOW:
        return "unknown"
    return verdict


def _head_health(runs: Sequence[Mapping[str, Any]], complete: bool, *, shared: bool, since: str = "") -> str:
    """One open head commit's verdict from its ``pull_request`` runs created at or after ``since``.

    A commit can be reused by a later PR (the same head against another
    base, after the first PR closed): runs older than the open PR belong to
    the old one, so they are not this PR's CI and are dropped.

    GitHub lists on a run every open PR whose head matches it, "not
    necessarily" the one that triggered it, and none for a fork PR. So when
    two open PRs share a head (one branch, two bases), their runs cannot be
    split per PR: one PR's green runs cannot prove the other PR reported CI
    at all, and that head reads ``unknown``, never ``success``. Likewise with
    an incomplete run list. In both cases only a failure already visible is
    reported.
    """
    verdict = _compute_health([run for run in runs if str(run.get("created_at", "")) >= since])
    if verdict == "partial":
        return verdict
    if shared or not complete:
        return "unknown"
    return verdict


def _open_pr_health(token: str, repo_slug: str) -> str:
    """The verdict over the newest run of each workflow on each OPEN PR's head commit.

    ``none`` when no PR is open. This used to filter the DEFAULT-branch runs
    for ``pull_request`` events, and a PR run never carries the default
    branch, so every repo read ``unknown``.

    Every open head gets its OWN pull_request-run lookup, so a stale PR's old
    failure, or an older failing workflow beside a newer passing one, still
    counts. Any failing head makes the repo ``partial``. Otherwise no verdict
    is claimed (``unknown``) while a head has no decisive run (none at all,
    or only cancelled/skipped ones), sits past the lookup cap, or more open
    PRs exist than one page lists: CI that never reported is not a pass.
    """
    # 403 as well as 404 degrades just this field (the Lane-8 contract): a
    # token can read a repo's metadata yet be denied its pull requests.
    pulls, headers = _github_fetch_degraded(f"{GITHUB_API_BASE}/repos/{repo_slug}/pulls?state=open&per_page=100", token)
    if not isinstance(pulls, list) or _next_page_url(headers or {}):
        return "unknown"
    prs_per_head = Counter(sha for sha in map(_head_sha, pulls) if sha)
    if not prs_per_head:
        return "none"
    # The oldest open PR's creation time per head: a run older than that is a
    # closed PR's. A PR without the field keeps every run (no filtering).
    opened: Dict[str, str] = {}
    for pull in pulls:
        if isinstance(pull, Mapping) and _head_sha(pull):
            created = str(pull.get("created_at") or "")
            opened[_head_sha(pull)] = min(opened.get(_head_sha(pull), created), created)
    heads = sorted(prs_per_head)
    verdicts = [
        _head_health(*_head_runs(token, repo_slug, sha), shared=prs_per_head[sha] > 1, since=opened.get(sha, ""))
        for sha in heads[:MAX_PR_HEAD_LOOKUPS]
    ]
    if "partial" in verdicts:
        return "partial"
    if len(heads) > MAX_PR_HEAD_LOOKUPS or "unknown" in verdicts:
        return "unknown"
    return "pending" if "pending" in verdicts else "success"


def _gate_status_from_context(context_name: str, contexts: Mapping[str, Dict[str, str]]) -> str:
    """Resolve one required context to ``pass``/``fail``/``pending``/``missing``.

    Mirrors ``build_quality_rollup._status_from_context`` (check-run and
    legacy status semantics identical), with one dashboard-specific nuance:
    a required context in ``PR_ONLY_CONTEXTS`` (the aggregate ``CodeQL``)
    that is absent on the default branch reports ``pr_only`` instead of
    ``missing`` — L7's phantom-context vocabulary, so observe-mode repos
    using CodeQL default setup don't render as permanently red.
    """
    details = _resolve_observed_context(context_name, contexts)
    if not details:
        return "pr_only" if context_name in PR_ONLY_CONTEXTS else "missing"
    if details.get("source") == "check_run" and details.get("state") != "completed":
        return "pending"
    if details.get("source") == "status" and details.get("conclusion") == "pending":
        return "pending"
    return "fail" if _evaluate_observed_context(context_name, details) else "pass"


def _aggregate_gate_status(gates: Sequence[Mapping[str, Any]]) -> str:
    """Reduce per-gate verdicts to one repo-level badge (fail > pending > missing > pass)."""
    if not gates:
        return "unknown"
    verdicts = {str(gate.get("status")) for gate in gates}
    for overall in ("fail", "pending", "missing", "pass"):
        if overall in verdicts:
            return overall
    return "unknown"


def _next_page_url(headers: Mapping[str, str]) -> str:
    """Extract the ``rel="next"`` URL from a GitHub Link header ("" if none)."""
    link = str(headers.get("link", "") or "")
    for part in link.split(","):
        piece = part.strip()
        if piece.endswith('rel="next"') and "<" in piece and ">" in piece:
            return piece[piece.index("<") + 1 : piece.index(">")]
    return ""


def _collect_all_check_runs(url: str, token: str) -> Tuple[List[Dict[str, Any]], bool]:
    """Gather check-runs across Link-header pages; return ``(runs, partial)``.

    A commit with re-run history can carry well over 100 check-runs (measured
    live 2026-10-21: tracelines HEAD has 120, and the first ``per_page=100``
    page holds only Analyze/canary re-runs — the required contexts sit on
    page 2). ``partial`` is True when the walk ended on a denial (403/404) or
    hit the page cap, so callers can flag the measurement as incomplete
    instead of silently reporting truncated ``missing`` verdicts.
    """
    runs: List[Dict[str, Any]] = []
    next_url: str | None = url
    for _ in range(MAX_CHECK_RUN_PAGES):
        if not next_url:
            return runs, False
        payload, headers = _github_fetch_degraded(next_url, token)
        if payload is None:
            return runs, True
        if isinstance(payload, Mapping):
            runs.extend(run for run in payload.get("check_runs", []) or [] if isinstance(run, Mapping))
        next_url = _next_page_url(headers or {}) or None
    return runs, True


def _lane_gates(
    token: str,
    repo_slug: str,
    default_branch: str,
    profile: Mapping[str, Any],
) -> Dict[str, Any]:
    """Measure the profile's required contexts on the default-branch HEAD.

    Every fetch is 404/403-tolerant (the Lane-8 staleness contract): a
    private repo, an empty branch, or a denied check feed degrades THIS
    repo's row to ``gate_status: stale`` with a reason — the build never
    fails because one fleet member could not be measured.
    """
    required, required_error = collect_required(profile)
    base: Dict[str, Any] = {
        "gate_status": "unknown",
        "gates": [],
        "gate_head_sha": "",
        "gate_head_date": "",
        "gate_note": required_error,
    }
    if required_error:
        return base
    head = _github_payload_or_degraded(f"{GITHUB_API_BASE}/repos/{repo_slug}/commits/{default_branch}", token)
    if not isinstance(head, Mapping) or not str(head.get("sha") or "").strip():
        base["gate_status"] = "stale"
        base["gate_note"] = f"default-branch head unavailable for {default_branch}"
        return base
    sha = str(head["sha"])
    commit_info = head.get("commit")
    head_date = ""
    if isinstance(commit_info, Mapping):
        committer = commit_info.get("committer")
        if isinstance(committer, Mapping):
            head_date = str(committer.get("date") or "")
    runs, partial = _collect_all_check_runs(
        f"{GITHUB_API_BASE}/repos/{repo_slug}/commits/{sha}/check-runs?per_page=100", token
    )
    statuses = _github_payload_or_degraded(f"{GITHUB_API_BASE}/repos/{repo_slug}/commits/{sha}/status", token)
    if not runs and statuses is None:
        base["gate_status"] = "stale"
        base["gate_note"] = "check-runs and status feeds unavailable (404/403)"
        base["gate_head_sha"] = sha
        base["gate_head_date"] = head_date
        return base
    contexts = _collect_contexts(
        {"check_runs": runs},
        statuses if isinstance(statuses, Mapping) else {},
    )
    gates = [{"context": name, "status": _gate_status_from_context(name, contexts)} for name in required]
    return {
        "gate_status": _aggregate_gate_status(gates),
        "gates": gates,
        "gate_head_sha": sha,
        "gate_head_date": head_date,
        "gate_note": "partial check-runs feed (page cap or mid-walk denial)" if partial else "",
    }


def _live_health(
    token: str,
    repo_slug: str,
    default_branch: str,
    profile: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    # Every per-repo fetch is 404-tolerant: a 404 means "this repo has no
    # such resource visible to the token" (private repo, no runs, no
    # rulesets) and must degrade that ONE row, never abort the whole
    # dashboard build.
    default_branch_health = _default_branch_health(token, repo_slug, default_branch)
    rulesets = _github_payload_or_none(f"{GITHUB_API_BASE}/repos/{repo_slug}/rulesets", token)
    # Fetch repo metadata to capture visibility for the redaction step
    # in ``build_dashboard_payload``. Using the same token lets private
    # repos (where the bot has read access) report their true visibility;
    # repos visible only as public report ``"public"``. A 404 here means
    # the token cannot see the repo AT ALL — a public repo's metadata is
    # visible to any token, so an unreadable governed repo is private (or
    # gone). Worst-case default to ``"private"`` so ``redact_private_repos``
    # masks the slug on the public dashboard instead of leaking it
    # (Phase 5 §8). Reachable-but-unusable metadata keeps ``"public"``.
    repo_meta = _github_payload_or_none(f"{GITHUB_API_BASE}/repos/{repo_slug}", token)
    visibility = "public"
    if repo_meta is None:
        visibility = "private"
    elif isinstance(repo_meta, dict):
        raw_visibility = repo_meta.get("visibility")
        if isinstance(raw_visibility, str) and raw_visibility.strip():
            visibility = raw_visibility.strip().lower()
    health: Dict[str, Any] = {
        "default_branch_health": default_branch_health,
        # A repo the token cannot see at all has no readable PRs either.
        "open_pr_health": _open_pr_health(token, repo_slug) if repo_meta is not None else "unknown",
        "ruleset_present": bool(rulesets),
        "visibility": visibility,
    }
    if profile is not None:
        health.update(_lane_gates(token, repo_slug, default_branch, profile))
    return health


def main() -> int:
    """Handle main."""
    args = parse_args()
    inventory = load_inventory(args.inventory) if args.inventory else load_inventory()
    profiles = {
        repo_entry["slug"]: load_repo_profile(inventory, repo_entry["slug"]) for repo_entry in inventory["repos"]
    }
    token = (os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")).strip()
    live = {}
    if token:
        for repo_entry in inventory["repos"]:
            live[repo_entry["slug"]] = _live_health(
                token,
                repo_entry["slug"],
                repo_entry.get("default_branch", "main"),
                profile=profiles.get(repo_entry["slug"]),
            )
    payload = build_dashboard_payload(inventory=inventory, profiles=profiles, live=live)
    docs_admin = (
        Path(args.assets_dir).resolve() if args.assets_dir else Path(__file__).resolve().parents[2] / "docs" / "admin"
    )
    assets_dir = docs_admin if docs_admin.exists() else None
    write_dashboard(Path(args.output_dir), payload, assets_dir=assets_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
