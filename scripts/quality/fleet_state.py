#!/usr/bin/env python3
"""Live fleet state for the alert detectors and the dashboard pages.

The bypass-stale and drift-stuck detectors (and the dashboard's drift page)
read state files that no job ever wrote, so they were never evaluated. Each
source here is something the platform can READ right now:

* break-glass follow-ups: the platform's open issues labelled
  ``quality-zero:break-glass-followup`` (opened by
  ``reusable-quality-zero-bypass.yml``);
* drift-sync PRs: the open pull requests of every repository the fleet
  declares (``inventory/repos.yml``) whose head branch is one
  ``apply_drift_pr.py`` creates.

``opened_at`` is a timezone-aware ``datetime``: the detectors subtract it
from ``now``, so a raw API string would raise.

A failed or unparseable ``gh`` call RAISES, and so does a listing that
reached its ``--limit``. Reading either as "no follow-ups" or "no drift"
would silently report an all-clear, and reconciling against a truncated
list would close alerts whose subject is still live.
"""

from __future__ import absolute_import

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess  # nosec B404 # noqa: S404 — gh CLI wrapper; args are controlled
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

if str(Path(__file__).resolve().parents[2]) not in sys.path:  # pragma: no cover - direct CLI use
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.apply_drift_pr import _BRANCH_PREFIX as DRIFT_BRANCH_PREFIX  # noqa: E402
from scripts.quality.control_plane import load_inventory  # noqa: E402
from scripts.quality.fleet_inventory import PRIVATE_INCLUDE_SLUGS  # noqa: E402

ProcessRunner = Callable[..., "subprocess.CompletedProcess[str]"]

BYPASS_FOLLOWUP_LABEL = "quality-zero:break-glass-followup"
#: A listing asks for one item more than this; getting it means the list may
#: be truncated, so the state is unknown rather than complete.
LIST_LIMIT = 200
INVENTORY_PATH = Path(__file__).resolve().parents[2] / "inventory" / "repos.yml"
#: The fleet-read PAT arrives here, never in GH_TOKEN: the workflow token
#: keeps every platform write, and private members' PRs are only visible to
#: the PAT (the same contract as the CVE sweep).
FLEET_READ_PAT_ENV = "QZ_FLEET_READ_TOKEN"
#: The fleet's private members: invisible to a public-only read, never "gone".
PRIVATE_SLUGS = frozenset(PRIVATE_INCLUDE_SLUGS)


class FleetStateError(RuntimeError):
    """A live source could not be read; the state is unknown, not empty."""


def _gh_json(
    args: Sequence[str],
    *,
    runner: ProcessRunner,
    env: Optional[Mapping[str, str]] = None,
    limit: Optional[int] = None,
) -> List[Mapping[str, Any]]:
    """Run one ``gh`` listing (in ``env`` when given) and return its JSON array of objects.

    Raises when the call fails, its output is not a JSON array, or it holds
    more than ``limit`` items (the caller asked for ``limit + 1``).
    """
    extra = {} if env is None else {"env": dict(env)}
    completed = runner(["gh", *args], capture_output=True, text=True, check=False, **extra)  # nosec B603
    if completed.returncode != 0:
        raise FleetStateError(f"`gh {' '.join(args[:2])}` exited {completed.returncode}: {completed.stderr.strip()}")
    try:
        payload = json.loads(completed.stdout or "[]")
    except ValueError as exc:
        raise FleetStateError(f"`gh {' '.join(args[:2])}` returned unparseable JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise FleetStateError(f"`gh {' '.join(args[:2])}` returned {type(payload).__name__}, expected a list")
    if limit is not None and len(payload) > limit:
        raise FleetStateError(f"`gh {' '.join(args[:2])}` returned more than {limit} items: the list may be truncated")
    return [item for item in payload if isinstance(item, Mapping)]


def _opened_at(raw: Any) -> dt.datetime:
    """Parse a GitHub ISO timestamp into an aware ``datetime``."""
    return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def bypass_followups(platform_slug: str, *, runner: ProcessRunner = subprocess.run) -> List[Dict[str, Any]]:
    """Every OPEN break-glass follow-up issue on the platform, as detector input."""
    issues = _gh_json(
        [
            "issue",
            "list",
            "--repo",
            platform_slug,
            "--label",
            BYPASS_FOLLOWUP_LABEL,
            "--state",
            "open",
            "--limit",
            str(LIST_LIMIT + 1),
            "--json",
            "number,title,createdAt,url",
        ],
        runner=runner,
        limit=LIST_LIMIT,
    )
    return [
        {
            "slug": platform_slug,
            "issue_number": int(issue["number"]),
            "opened_at": _opened_at(issue["createdAt"]),
            "title": str(issue.get("title", "")),
            "url": str(issue.get("url", "")),
        }
        for issue in issues
    ]


PRIVATE_SUBJECT_PREFIX = "<private:"


def public_slug(slug: str) -> str:
    """The slug as it may appear on a PUBLIC surface (the dashboard, a platform alert issue).

    A private member's name is replaced by a stable, non-reversible tag, so an
    alert still dedupes per repository without disclosing it.
    """
    if slug not in PRIVATE_SLUGS:
        return slug
    return f"{PRIVATE_SUBJECT_PREFIX}{hashlib.sha256(slug.encode('utf-8')).hexdigest()[:8]}>"


def fleet_read_env(environ: Mapping[str, str]) -> Optional[Dict[str, str]]:
    """``GH_TOKEN`` swapped for the fleet-read PAT when one is set, else ``None`` (public repos only)."""
    token = str(environ.get(FLEET_READ_PAT_ENV, "")).strip()
    return {**environ, "GH_TOKEN": token} if token else None


def drift_search_scope(environ: Mapping[str, str]) -> str:
    """What the drift read can see: say so wherever its result is reported."""
    if fleet_read_env(environ) is not None:
        return "fleet-wide (fleet-read token)"
    return "public repositories only (no CODEX_FLEET_READ_PAT: private members' drift PRs are not visible)"


def fleet_slugs(inventory_path: Path | str = INVENTORY_PATH) -> List[str]:
    """The declared fleet: only these repositories' PRs are fleet state, not every repository of the owner."""
    repos = load_inventory(inventory_path).get("repos", [])
    return sorted({str(repo["slug"]) for repo in repos if isinstance(repo, Mapping) and repo.get("slug")})


def _is_drift_branch(head: str) -> bool:
    """A head branch ``apply_drift_pr`` creates: ``<prefix>/<run-id>`` (or the bare prefix)."""
    return head == DRIFT_BRANCH_PREFIX or head.startswith(f"{DRIFT_BRANCH_PREFIX}/")


def drift_sync_prs(
    slugs: Sequence[str],
    *,
    runner: ProcessRunner = subprocess.run,
    environ: Optional[Mapping[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Every OPEN drift-sync pull request in the declared fleet ``slugs``, as detector input.

    Each repository's open PRs are listed and their ACTUAL head branch is
    matched against the prefix ``apply_drift_pr`` uses, so neither search
    semantics nor a repository outside the fleet can change the result.
    Without the fleet-read PAT, private members are not read
    (``drift_search_scope`` says so). A private member's record carries
    ``visibility: private``, a masked slug and no URL, and a failed read
    never names it: every consumer (the public dashboard, the platform's
    public alert issues and workflow logs) sees only the tag.
    """
    env = fleet_read_env(os.environ if environ is None else environ)
    records: List[Dict[str, Any]] = []
    for slug in slugs:
        private = slug in PRIVATE_SLUGS
        if private and env is None:
            continue
        try:
            prs = _gh_json(
                [
                    "pr",
                    "list",
                    "--repo",
                    slug,
                    "--state",
                    "open",
                    "--limit",
                    str(LIST_LIMIT + 1),
                    "--json",
                    "number,headRefName,createdAt,url",
                ],
                runner=runner,
                env=env,
                limit=LIST_LIMIT,
            )
        except FleetStateError as exc:
            if private:
                raise FleetStateError(f"listing the open PRs of {public_slug(slug)} failed") from None
            raise FleetStateError(f"{slug}: {exc}") from exc
        records.extend(
            {
                "slug": public_slug(slug),
                "pr_number": int(pr["number"]),
                "opened_at": _opened_at(pr["createdAt"]),
                "status": "open",
                "pr_url": "" if private else str(pr.get("url", "")),
                "visibility": "private" if private else "public",
            }
            for pr in prs
            if _is_drift_branch(str(pr.get("headRefName", "")))
        )
    return records


def to_jsonl(records: Sequence[Mapping[str, Any]]) -> str:
    """One JSON object per line, datetimes as ISO strings (the dashboard pages' input)."""
    return "".join(
        json.dumps({key: value.isoformat() if isinstance(value, dt.datetime) else value for key, value in r.items()})
        + "\n"
        for r in records
    )


def main(argv: Optional[Sequence[str]] = None, *, runner: ProcessRunner = subprocess.run) -> int:
    """CLI: write the live drift-sync PRs as JSONL for the dashboard's drift page."""
    parser = argparse.ArgumentParser(description="Write live fleet state for the dashboard.")
    parser.add_argument("--inventory", default=str(INVENTORY_PATH), help="the declared fleet (inventory/repos.yml)")
    parser.add_argument("--drift-jsonl", required=True)
    args = parser.parse_args(argv)
    try:
        records = drift_sync_prs(fleet_slugs(args.inventory), runner=runner)
    except FleetStateError as exc:
        print(f"fleet_state: {exc}", file=sys.stderr)
        return 1
    out = Path(args.drift_jsonl)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_jsonl(records), encoding="utf-8", newline="\n")
    print(f"fleet_state: {len(records)} open drift-sync PR(s) -> {out}; scope: {drift_search_scope(os.environ)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
