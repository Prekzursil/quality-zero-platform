#!/usr/bin/env python3
"""Lane-8 dashboard extras: ruleset-drift and rollup-gap payloads.

Extends the existing GitHub Pages admin dashboard (built by
``publish-admin-dashboard.yml``) with two additional data files the SPA
renders, so an operator can answer two questions without cloning anything:

* **Where does committed-vs-generated ruleset/profile drift exist?**
  ``build_ruleset_drift_payload`` regenerates the ruleset payloads from the
  profiles + inventory (the exact generation ``scripts/verify`` and the weekly
  ``fleet-health.yml`` sweep run) into a throwaway directory and byte-compares
  the result against the committed ``generated/rulesets`` artifacts. The
  report mirrors ``drift_sync.py``'s status vocabulary
  (``in_sync`` / ``drift`` / ``missing``).

* **Which open PRs lack a rollup comment?**
  ``collect_rollup_gaps`` lists the open PRs of every enrolled repo and
  checks the PR conversation for the sticky rollup comment posted by
  ``post_pr_quality_comment.py`` (detected via its ``MARKER``).

Per-repo GitHub calls are 404-tolerant (the ``build_admin_dashboard``
contract): an unreadable/private/stale source degrades THAT repo to a
``stale`` row with a reason and never fails the build. Nothing but static
JSON reaches the browser — no tokens client-side, private slugs are masked
before serialisation (``redact_private_repos``).
"""

from __future__ import absolute_import

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence
from urllib.error import HTTPError

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # pragma: no cover — direct CLI invocation

from scripts.quality.admin_dashboard_pages import redact_private_repos
from scripts.quality.build_admin_dashboard import GITHUB_API_BASE, _github_payload
from scripts.quality.common import utc_timestamp
from scripts.quality.control_plane import load_inventory
from scripts.quality.generate_ruleset_payload import generate_rulesets
from scripts.quality.post_pr_quality_comment import MARKER as ROLLUP_MARKER


def _fetch_or_none(url: str, token: str) -> Any:
    """Fetch ``url``; ``None`` on 404 OR 403 (per-repo degradation, Lane 8).

    A token can read a member repo's metadata while being denied its pulls
    or comments feed, and GitHub reports an unreadable private repo as 404 —
    either way the dashboard build must degrade that ONE repo to a stale row
    instead of failing (same contract as the gate panel's
    ``_github_payload_or_degraded``). Any other ``HTTPError`` propagates.
    """
    try:
        return _github_payload(url, token)
    except HTTPError as exc:
        if exc.code in (403, 404):
            return None
        raise


# Injectable REST fetcher (tests pass a fake; production uses the
# 404/403-tolerant ``_fetch_or_none`` above).
Fetcher = Callable[[str, str], Any]

DEFAULT_INVENTORY = "inventory/repos.yml"
DEFAULT_COMMITTED_RULESETS = "generated/rulesets"
DEFAULT_OUTPUT_DIR = "site/data"
DEFAULT_PR_LIMIT = 30

_PRIVATE_REASON = "private or unreadable with the build token"


def _entries_for(directory: Path) -> Dict[str, bytes]:
    """Return ``{filename: bytes}`` for every ``*.json`` in ``directory``."""
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(directory.glob("*.json"))}


def compare_ruleset_dirs(committed_dir: Path, regenerated_dir: Path) -> Dict[str, Any]:
    """Byte-compare two ruleset directories into a drift report.

    Statuses mirror ``drift_sync.py``: ``in_sync`` (identical bytes),
    ``drift`` (both sides carry the file, bytes differ), ``missing`` (the
    file exists on only one side — ``side`` says which). Pure function over
    plain directories so the determinism contract is unit-testable without
    touching the control plane.
    """
    committed = _entries_for(committed_dir)
    regenerated = _entries_for(regenerated_dir)
    entries: List[Dict[str, Any]] = []
    for name in sorted(set(committed) | set(regenerated)):
        if name not in committed:
            status, side = "missing", "generated_only"
        elif name not in regenerated:
            status, side = "missing", "committed_only"
        elif committed[name] == regenerated[name]:
            status, side = "in_sync", "both"
        else:
            status, side = "drift", "both"
        entries.append({"file": name, "status": status, "side": side})
    summary = dict.fromkeys(("in_sync", "drift", "missing"), 0)
    for entry in entries:
        summary[entry["status"]] += 1
    return {
        "status": "in_sync" if summary["drift"] == 0 and summary["missing"] == 0 else "drift",
        "summary": summary,
        "entries": entries,
    }


def build_ruleset_drift_payload(
    *,
    inventory_path: Path | str,
    committed_dir: Path | str,
) -> Dict[str, Any]:
    """Regenerate the ruleset payloads and diff them against the committed set."""
    inventory = load_inventory(str(inventory_path))
    with tempfile.TemporaryDirectory(prefix="qzp-ruleset-regen-") as tmp:
        regenerated_dir = Path(tmp) / "rulesets"
        generate_rulesets(inventory, regenerated_dir, verbose=False)
        report = compare_ruleset_dirs(Path(committed_dir), regenerated_dir)
    report["generated_at"] = utc_timestamp()
    report["committed_dir"] = str(committed_dir).replace("\\", "/")
    return report


def pr_has_rollup(comments: Sequence[Mapping[str, Any]]) -> bool:
    """Return whether any PR conversation comment carries the rollup marker."""
    return any(ROLLUP_MARKER in str(comment.get("body") or "") for comment in comments)


def _pull_entries(pulls: Sequence[Any], slug: str, token: str, fetch: Fetcher) -> List[Dict[str, Any]]:
    """Build one ``prs`` entry per open pull request of ``slug``."""
    entries: List[Dict[str, Any]] = []
    for pull in pulls:
        if not isinstance(pull, Mapping):
            continue
        number = pull.get("number")
        comments = fetch(f"{GITHUB_API_BASE}/repos/{slug}/issues/{number}/comments", token)
        rollup = ("yes" if pr_has_rollup(comments) else "no") if isinstance(comments, list) else "unknown"
        entries.append(
            {
                "number": number,
                "title": str(pull.get("title") or ""),
                "url": str(pull.get("html_url") or ""),
                "rollup_comment": rollup,
            }
        )
    return entries


def collect_rollup_gaps(
    entries: Sequence[Mapping[str, Any]],
    token: str,
    *,
    fetch: Fetcher = _fetch_or_none,
    pr_limit: int = DEFAULT_PR_LIMIT,
) -> Dict[str, Any]:
    """Return the per-repo open-PR rollup-comment coverage payload.

    ``entries`` are inventory rows (``slug`` required). Repos whose REST
    source is absent (private members the build token cannot read) degrade
    to ``status: stale`` rows carrying a reason — never an exception.
    """
    repos: List[Dict[str, Any]] = []
    for entry in entries:
        slug = str(entry.get("slug", "")).strip()
        if not slug:
            continue
        meta = fetch(f"{GITHUB_API_BASE}/repos/{slug}", token)
        visibility = "private"
        if isinstance(meta, Mapping):
            visibility = str(meta.get("visibility") or "private").strip().lower()
        if visibility == "internal":
            # Personal-account tokens never see this, but normalise anyway so
            # ``redact_private_repos`` masks org-internal members too.
            visibility = "private"
        if meta is None or visibility == "private":
            repos.append({"slug": slug, "visibility": visibility, "status": "stale", "reason": _PRIVATE_REASON})
            continue
        pulls = fetch(f"{GITHUB_API_BASE}/repos/{slug}/pulls?state=open&per_page={pr_limit}", token)
        if not isinstance(pulls, list):
            repos.append(
                {"slug": slug, "visibility": "public", "status": "stale", "reason": "open PRs unavailable (404)"}
            )
            continue
        prs = _pull_entries(pulls, slug, token, fetch)
        repos.append(
            {
                "slug": slug,
                "visibility": "public",
                "status": "ok",
                "reason": "",
                "open_count": len(prs),
                "gaps": sum(1 for pr in prs if pr["rollup_comment"] == "no"),
                "truncated": len(pulls) >= pr_limit,
                "prs": prs,
            }
        )
    return {
        "generated_at": utc_timestamp(),
        "marker": ROLLUP_MARKER,
        "repos": redact_private_repos(repos),
    }


def _redact_gaps_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Mask PR titles/urls of private rows whose slug was redacted."""
    repos = payload.get("repos", [])
    masked: List[Dict[str, Any]] = []
    for row in repos:
        record = dict(row)
        if str(record.get("slug", "")) == "<private>":
            for pr in record.get("prs", []):
                pr["title"] = "<private>"
                pr["url"] = ""
        masked.append(record)
    return {"generated_at": payload.get("generated_at", ""), "marker": payload.get("marker", ""), "repos": masked}


def parse_args() -> argparse.Namespace:
    """Handle parse args."""
    parser = argparse.ArgumentParser(description="Build the Lane-8 dashboard extras payload.")
    parser.add_argument("--inventory", default=DEFAULT_INVENTORY)
    parser.add_argument("--committed-rulesets", default=DEFAULT_COMMITTED_RULESETS)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--slug-filter",
        default="",
        help="Optional substring filter — only repos whose slug contains this are scanned.",
    )
    return parser.parse_args()


def main() -> int:
    """Handle main."""
    args = parse_args()
    root = Path(__file__).resolve().parents[2]
    inventory_path = Path(args.inventory)
    if not inventory_path.is_absolute():
        inventory_path = root / inventory_path
    inventory = load_inventory(str(inventory_path))
    rows: List[Dict[str, Any]] = list(inventory.get("repos", []))
    slug_filter = args.slug_filter.strip()
    if slug_filter:
        rows = [row for row in rows if slug_filter in str(row.get("slug", ""))]

    committed = Path(args.committed_rulesets)
    if not committed.is_absolute():
        committed = root / committed
    drift_payload = build_ruleset_drift_payload(inventory_path=inventory_path, committed_dir=committed)

    token = (os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")).strip()
    if token:
        gaps_payload = collect_rollup_gaps(rows, token)
    else:
        gaps_payload = {
            "generated_at": utc_timestamp(),
            "marker": ROLLUP_MARKER,
            "repos": [
                {"slug": str(row.get("slug", "")), "visibility": "public", "status": "stale", "reason": "no token"}
                for row in rows
            ],
        }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "ruleset-drift.json").write_text(
        json.dumps(drift_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "rollup-gaps.json").write_text(
        json.dumps(_redact_gaps_payload(gaps_payload), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
