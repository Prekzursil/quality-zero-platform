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

A failed or unparseable ``gh`` call, or a listing that reached its
``--limit``, is never read as "no follow-ups" or "no drift": that would
silently report an all-clear, and reconciling against a truncated list would
close alerts whose subject is still live. The single follow-up listing
RAISES. A fleet member whose PRs cannot be read is returned as UNREAD, so
the rest of the fleet is still evaluated and every consumer can say which
part is unknown.
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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

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
#: The fleet's known private members (lower-cased: GitHub slugs are case-insensitive).
PRIVATE_SLUGS = frozenset(slug.lower() for slug in PRIVATE_INCLUDE_SLUGS)


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


def public_slug(slug: str, *, private: Optional[bool] = None) -> str:
    """The slug as it may appear on a PUBLIC surface (the dashboard, a platform alert issue).

    A private member's name is replaced by a stable, non-reversible tag
    (hashed case-insensitively), so an alert still dedupes per repository
    without disclosing it. ``private`` overrides the known-member list with a
    live visibility check (``repo_is_private``).
    """
    if not (slug.lower() in PRIVATE_SLUGS if private is None else private):
        return slug
    return f"{PRIVATE_SUBJECT_PREFIX}{hashlib.sha256(slug.lower().encode('utf-8')).hexdigest()[:8]}>"


def repo_visibility(slug: str, *, runner: ProcessRunner = subprocess.run) -> str:
    """``"public"``, ``"private"`` or ``"unknown"`` for one fleet member, read with the workflow token.

    A known private member is private without a call. Otherwise the API
    decides; a failed or odd answer is ``unknown``, and the caller treats it
    as private for display (masked) and does not read the repository this run.
    """
    if slug.lower() in PRIVATE_SLUGS:
        return "private"
    completed = runner(["gh", "api", f"repos/{slug}", "--jq", ".private"], capture_output=True, text=True, check=False)
    answer = str(completed.stdout or "").strip() if completed.returncode == 0 else ""
    return {"false": "public", "true": "private"}.get(answer, "unknown")


def repo_is_private(slug: str, *, runner: ProcessRunner = subprocess.run) -> bool:
    """Whether ``slug`` must be masked. Fails CLOSED: anything not proven public is masked."""
    return repo_visibility(slug, runner=runner) != "public"


def unverifiable_subject(subject: str, unread: Mapping[str, str]) -> bool:
    """Whether an alert subject (``<repo>#<n>``) belongs to a member this run did not read.

    The subject may carry the RAW slug (opened while the repository was
    public) while ``unread`` is keyed by its masked tag (it turned private,
    or its visibility could not be read), or the other way round (opened
    while private, unread now as a public repository): both forms are
    recognized, so a visibility change or a transient failure is never
    mistaken for a resolved alert.
    """
    repo = subject.rsplit("#", 1)[0]
    # Both directions: a raw unread key also answers for its masked tag (an
    # alert opened while the repository was private, read failing now).
    names = set(unread) | {public_slug(key, private=True) for key in unread}
    return repo in names or public_slug(repo, private=True) in names


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
    """The declared fleet: only these repositories' PRs are fleet state, not every repository of the owner.

    Raises when it declares none: an empty roster would read as "no drift"
    and close every open drift alert.
    """
    repos = load_inventory(inventory_path).get("repos", [])
    slugs = sorted({str(repo["slug"]) for repo in repos if isinstance(repo, Mapping) and repo.get("slug")})
    if not slugs:
        raise FleetStateError(f"{inventory_path} declares no fleet repository")
    return slugs


def _is_drift_branch(head: str) -> bool:
    """A head branch ``apply_drift_pr`` creates: ``<prefix>/<run-id>`` (or the bare prefix)."""
    return head == DRIFT_BRANCH_PREFIX or head.startswith(f"{DRIFT_BRANCH_PREFIX}/")


def drift_sync_prs(
    slugs: Sequence[str],
    *,
    runner: ProcessRunner = subprocess.run,
    environ: Optional[Mapping[str, str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """``(open drift-sync PRs, unread members)`` for the declared fleet ``slugs``.

    Each repository's open PRs are listed. A PR counts only when its ACTUAL
    head branch is one ``apply_drift_pr`` creates AND it is not from a fork
    (``isCrossRepository``): a fork branch can mimic the name, not the
    platform. Neither search semantics nor a repository outside the fleet
    can change the result.

    ``unread`` maps a member's public tag to why it was not read: a private
    member without the fleet-read PAT, or a failed or truncated listing.
    Its drift is UNKNOWN, not "none": the dashboard says the read is
    partial, and its open drift alerts are not closed. A private member's
    record carries ``visibility: private``, a masked slug and no URL, and its
    failure reason never names it: every consumer (the public dashboard, the
    platform's public alert issues and workflow logs) sees only the tag.
    """
    env = fleet_read_env(os.environ if environ is None else environ)
    records: List[Dict[str, Any]] = []
    unread: Dict[str, str] = {}
    rejected = False  # the fleet-read PAT answered 401: public members fall back to the workflow token
    for slug in slugs:
        visibility = repo_visibility(slug, runner=runner)
        private = visibility != "public"
        tag = public_slug(slug, private=private)
        if visibility == "unknown":
            unread[tag] = "its visibility could not be read"
            continue
        if private and (env is None or rejected):
            unread[tag] = f"private, and {'the fleet-read token was rejected' if rejected else 'no fleet-read token'}"
            continue
        try:
            prs = _open_prs(slug, runner=runner, env=None if rejected else env)
        except FleetStateError as exc:
            if env is None or rejected or not _token_rejected(exc):
                unread[tag] = "the read failed" if private else str(exc)
                continue
            rejected = True
            if private:
                unread[tag] = "private, and the fleet-read token was rejected"
                continue
            try:
                prs = _open_prs(slug, runner=runner, env=None)
            except FleetStateError as retry:
                unread[tag] = str(retry)
                continue
        records.extend(
            {
                "slug": tag,
                "pr_number": int(pr["number"]),
                "opened_at": _opened_at(pr["createdAt"]),
                "status": "open",
                "pr_url": "" if private else str(pr.get("url", "")),
                "visibility": "private" if private else "public",
            }
            for pr in prs
            if pr.get("isCrossRepository") is False and _is_drift_branch(str(pr.get("headRefName", "")))
        )
    return records, unread


def _open_prs(slug: str, *, runner: ProcessRunner, env: Optional[Mapping[str, str]]) -> List[Mapping[str, Any]]:
    """One fleet member's open PRs (raises like ``_gh_json``, including past ``LIST_LIMIT``)."""
    return _gh_json(
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
            "number,headRefName,isCrossRepository,createdAt,url",
        ],
        runner=runner,
        env=env,
        limit=LIST_LIMIT,
    )


def _token_rejected(exc: FleetStateError) -> bool:
    """``gh`` failed because the token itself was refused (expired or revoked), not because of the repository."""
    text = str(exc)
    return "HTTP 401" in text or "Bad credentials" in text


def partial_marker(*, read: int, unread: int) -> Dict[str, Any]:
    """The JSONL line that tells the drift page its rows cover only ``read`` of the fleet's repositories."""
    return {"kind": "partial", "read": read, "unread": unread}


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
        slugs = fleet_slugs(args.inventory)
    except FleetStateError as exc:
        print(f"fleet_state: {exc}", file=sys.stderr)
        return 1
    records, unread = drift_sync_prs(slugs, runner=runner)
    for tag, reason in unread.items():
        print(f"::warning::fleet_state: {tag} was not read ({reason}); its drift is unknown")
    if slugs and len(unread) == len(slugs):
        print("fleet_state: no fleet repository could be read; writing nothing", file=sys.stderr)
        return 1
    out = Path(args.drift_jsonl)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = records + ([partial_marker(read=len(slugs) - len(unread), unread=len(unread))] if unread else [])
    out.write_text(to_jsonl(lines), encoding="utf-8", newline="\n")
    print(
        f"fleet_state: {len(records)} open drift-sync PR(s) in {len(slugs) - len(unread)} of {len(slugs)} "
        f"repositories -> {out}; scope: {drift_search_scope(os.environ)}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
