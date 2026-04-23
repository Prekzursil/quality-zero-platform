"""Fleet inventory (docs/QZP-V2-DESIGN.md §2).

Produces a canonical list of the repos the quality-zero-platform should
govern and diffs it against the hand-maintained ``inventory/repos.yml``.

This first slice is the pure logic core — filter + diff — with no I/O.
The ``gh api`` fetch and ``alert:repo-not-profiled`` issue-opener land
in subsequent commits so each layer can be tested in isolation.

Fleet filter (per Round 1 interview answer, captured in §2):

* Owner: ``Prekzursil``.
* Visibility: public only…
* …with exactly one explicit exception: ``Prekzursil/pbinfo-get-unsolved``
  is private but MUST be governed.
* Forks are excluded.
* Archived repos are *not* excluded — they stay in the fleet and carry
  their profile, which can flag them read-only if appropriate.
* GitHub template repos are excluded.
"""

from __future__ import absolute_import

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, FrozenSet, Iterable, List, Mapping, Sequence, Set

import yaml  # type: ignore[import-untyped]


# A process runner callable compatible with ``subprocess.run``. Tests inject
# a fake to avoid shelling out to the real ``gh`` binary.
ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


# The canonical list of private-repo exceptions. Kept tiny and explicit
# so adding a new private repo to the fleet requires a code change + PR,
# rather than a silent data-only edit.
PRIVATE_INCLUDE_SLUGS: FrozenSet[str] = frozenset(
    {
        "Prekzursil/pbinfo-get-unsolved",
    }
)


@dataclass(frozen=True)
class FleetDiff:
    """Summary of the gap between expected fleet and inventory file."""

    expected: List[str]
    """Slugs the fleet filter says SHOULD be governed."""

    inventoried: List[str]
    """Slugs currently listed in ``inventory/repos.yml``."""

    missing: List[str]
    """Expected slugs not yet in the inventory (→ open alert:repo-not-profiled)."""

    dead: List[str]
    """Inventoried slugs that no longer appear on GitHub (→ manual review)."""


def _slug_from_github_repo(repo: Mapping[str, Any]) -> str | None:
    """Return the ``owner/name`` slug from a ``gh api`` repo JSON record."""
    full_name = repo.get("full_name")
    if isinstance(full_name, str) and "/" in full_name:
        return full_name
    owner = repo.get("owner", {})
    owner_login = owner.get("login") if isinstance(owner, Mapping) else None
    name = repo.get("name")
    if isinstance(owner_login, str) and isinstance(name, str):
        return f"{owner_login}/{name}"
    return None


def _should_include_repo(repo: Mapping[str, Any]) -> bool:
    """Apply the fleet filter to one ``gh api`` repo record."""
    if repo.get("fork"):
        return False
    if repo.get("is_template"):
        return False
    slug = _slug_from_github_repo(repo)
    if slug is None:
        return False
    private = bool(repo.get("private"))
    if private and slug not in PRIVATE_INCLUDE_SLUGS:
        return False
    return True


def build_expected_fleet(github_repos: Iterable[Mapping[str, Any]]) -> List[str]:
    """Return the sorted list of slugs the fleet filter says to govern."""
    expected: Set[str] = set()
    for repo in github_repos:
        if not _should_include_repo(repo):
            continue
        slug = _slug_from_github_repo(repo)
        if slug is not None:
            expected.add(slug)
    return sorted(expected)


def load_inventory_slugs(inventory_path: Path) -> List[str]:
    """Return the sorted slugs currently declared in ``inventory/repos.yml``."""
    payload = yaml.safe_load(inventory_path.read_text(encoding="utf-8")) or {}
    repos = payload.get("repos") if isinstance(payload, Mapping) else []
    if not isinstance(repos, list):
        return []
    slugs: Set[str] = set()
    for item in repos:
        if not isinstance(item, Mapping):
            continue
        slug = item.get("slug")
        if isinstance(slug, str) and slug.strip():
            slugs.add(slug.strip())
    return sorted(slugs)


def diff_fleet(expected: Iterable[str], inventoried: Iterable[str]) -> FleetDiff:
    """Compute the missing + dead slug sets between expected and inventoried."""
    expected_sorted = sorted(set(expected))
    inventoried_sorted = sorted(set(inventoried))
    expected_set = set(expected_sorted)
    inventoried_set = set(inventoried_sorted)
    return FleetDiff(
        expected=expected_sorted,
        inventoried=inventoried_sorted,
        missing=sorted(expected_set - inventoried_set),
        dead=sorted(inventoried_set - expected_set),
    )


def _run_gh(
    args: Sequence[str],
    *,
    runner: ProcessRunner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    """Invoke ``gh`` with the given args, capturing stdout/stderr as text.

    Separated so tests can inject a mock runner without touching the real
    ``gh`` binary. ``check=True`` propagates failures as
    ``subprocess.CalledProcessError`` so the caller gets a clean signal.
    """
    return runner(
        ["gh", *list(args)],
        check=True,
        capture_output=True,
        text=True,
    )


def fetch_user_repos(
    owner: str,
    *,
    runner: ProcessRunner = subprocess.run,
    per_page: int = 100,
) -> List[Mapping[str, Any]]:
    """Return every GitHub repo owned by ``owner`` via ``gh api``.

    Uses ``--paginate`` + ``--slurp`` so all pages land in a single JSON
    array; ``gh`` already handles rate-limiting + auth.

    The ``/users/{owner}/repos`` endpoint only returns public repos. The
    caller runs a second ``/user/repos?visibility=all`` pass when a token
    with permission to see private repos is available so private
    exceptions (per ``PRIVATE_INCLUDE_SLUGS``) can be resolved.
    """
    args = [
        "api",
        "--paginate",
        "--slurp",
        "-H",
        "Accept: application/vnd.github+json",
        f"/users/{owner}/repos?per_page={per_page}&type=owner",
    ]
    completed = _run_gh(args, runner=runner)
    return _flatten_paginated_json(completed.stdout)


def fetch_authenticated_repos(
    *,
    runner: ProcessRunner = subprocess.run,
    per_page: int = 100,
) -> List[Mapping[str, Any]]:
    """Return every repo visible to the authenticated token (incl. private).

    Complements :func:`fetch_user_repos` by covering private repos the
    owner-scoped endpoint hides. Callers de-duplicate by ``full_name``.
    """
    args = [
        "api",
        "--paginate",
        "--slurp",
        "-H",
        "Accept: application/vnd.github+json",
        f"/user/repos?per_page={per_page}&visibility=all&affiliation=owner",
    ]
    completed = _run_gh(args, runner=runner)
    return _flatten_paginated_json(completed.stdout)


def _flatten_paginated_json(raw: str) -> List[Mapping[str, Any]]:
    """Flatten ``gh api --paginate --slurp`` output into a single repo list.

    ``--slurp`` yields a JSON array-of-arrays (one sub-array per page).
    Older ``gh`` versions without ``--slurp`` return a single flat array.
    Accept both shapes so callers don't have to branch on ``gh`` version.
    """
    payload = json.loads(raw) if raw else []
    if not isinstance(payload, list):
        return []
    flattened: List[Mapping[str, Any]] = []
    for item in payload:
        if isinstance(item, list):
            flattened.extend(entry for entry in item if isinstance(entry, Mapping))
        elif isinstance(item, Mapping):
            flattened.append(item)
    return flattened


def merge_repo_lists(
    *lists: Iterable[Mapping[str, Any]],
) -> List[Mapping[str, Any]]:
    """Merge several repo lists, de-duplicating by ``full_name``.

    Earlier lists win on slug collisions — useful when the ``/user/repos``
    endpoint returns a richer record than ``/users/{owner}/repos`` for the
    same repo but we want the first-seen record for determinism.
    """
    seen: Set[str] = set()
    merged: List[Mapping[str, Any]] = []
    for repo_list in lists:
        for repo in repo_list:
            slug = _slug_from_github_repo(repo)
            if slug is None or slug in seen:
                continue
            seen.add(slug)
            merged.append(repo)
    return merged


# ---------------------------------------------------------------------------
# alert:repo-not-profiled issue opener (docs/QZP-V2-DESIGN.md §8).
# Keeps the platform's issue tracker as the single source of alert truth —
# per-event only, no digests. Dedupes on issue title so re-running the
# inventory sweep doesn't spam duplicate issues.
# ---------------------------------------------------------------------------


ALERT_LABEL_NOT_PROFILED = "alert:repo-not-profiled"


def alert_issue_title(slug: str) -> str:
    """Canonical, dedupable title for the repo-not-profiled alert."""
    return f"[{ALERT_LABEL_NOT_PROFILED}] {slug}"


def find_existing_alert_issue(
    platform_slug: str,
    *,
    slug: str,
    runner: ProcessRunner = subprocess.run,
) -> Mapping[str, Any] | None:
    """Return the matching open alert issue for ``slug`` if one exists."""
    args = [
        "issue",
        "list",
        "--repo",
        platform_slug,
        "--label",
        ALERT_LABEL_NOT_PROFILED,
        "--state",
        "open",
        "--search",
        slug,
        "--json",
        "number,title,state",
        "--limit",
        "100",
    ]
    completed = _run_gh(args, runner=runner)
    payload = json.loads(completed.stdout) if completed.stdout else []
    if not isinstance(payload, list):
        return None
    expected_title = alert_issue_title(slug)
    for issue in payload:
        if isinstance(issue, Mapping) and issue.get("title") == expected_title:
            return issue
    return None


def open_alert_issue_for_unprofiled_repo(
    platform_slug: str,
    *,
    slug: str,
    runner: ProcessRunner = subprocess.run,
    dry_run: bool = False,
) -> Mapping[str, Any]:
    """Open (or re-use) an ``alert:repo-not-profiled`` issue for ``slug``.

    Returns the issue record as ``{"number": int, "title": str, "created": bool}``.
    When ``dry_run`` is true, no ``gh`` call is made and ``created`` is ``False``.
    """
    if dry_run:
        return {"number": 0, "title": alert_issue_title(slug), "created": False}

    existing = find_existing_alert_issue(platform_slug, slug=slug, runner=runner)
    if existing is not None:
        return {
            "number": int(existing.get("number", 0)),
            "title": str(existing.get("title", "")),
            "created": False,
        }

    body = (
        f"Repository `{slug}` appears in the fleet filter "
        f"(see `scripts/quality/fleet_inventory.py`) but has no entry in "
        f"`profiles/repos/` or `inventory/repos.yml`.\n\n"
        f"To close: add `profiles/repos/{slug.split('/', 1)[-1]}.yml`, "
        f"register the slug in `inventory/repos.yml`, and run "
        f"`python scripts/quality/fleet_inventory.py --check` locally to "
        f"confirm the gap closed."
    )
    args = [
        "issue",
        "create",
        "--repo",
        platform_slug,
        "--title",
        alert_issue_title(slug),
        "--label",
        ALERT_LABEL_NOT_PROFILED,
        "--body",
        body,
    ]
    completed = _run_gh(args, runner=runner)
    stdout = completed.stdout.strip()
    issue_number = _issue_number_from_create_output(stdout)
    return {
        "number": issue_number,
        "title": alert_issue_title(slug),
        "created": True,
    }


def close_alert_issue_for_profiled_repo(
    platform_slug: str,
    *,
    slug: str,
    runner: ProcessRunner = subprocess.run,
    dry_run: bool = False,
) -> Mapping[str, Any]:
    """Close any open ``alert:repo-not-profiled`` issue for ``slug``.

    Returns ``{"number": int, "closed": bool}``. When no matching open
    issue exists, ``closed`` is ``False`` and ``number`` is ``0``.
    """
    if dry_run:
        return {"number": 0, "closed": False}

    existing = find_existing_alert_issue(platform_slug, slug=slug, runner=runner)
    if existing is None:
        return {"number": 0, "closed": False}

    issue_number = int(existing.get("number", 0))
    args = [
        "issue",
        "close",
        str(issue_number),
        "--repo",
        platform_slug,
        "--comment",
        f"`{slug}` was profiled. Closing.",
    ]
    _run_gh(args, runner=runner)
    return {"number": issue_number, "closed": True}


def _issue_number_from_create_output(stdout: str) -> int:
    """Parse ``gh issue create`` URL output to extract the issue number.

    ``gh`` prints the issue URL on stdout: ``https://github.com/.../issues/123``.
    """
    if not stdout:
        return 0
    # Grab the trailing numeric component of the URL.
    tail = stdout.rstrip().rsplit("/", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return 0
