"""Fleet inventory — see ``docs/QZP-V2-DESIGN.md`` §2 for the contract."""
# Filter + diff + gh wrapper + alert opener for the governed-repo roster.
# The module splits cleanly into: (1) pure filter/diff logic, (2) the
# ``gh api`` fetch wrapper, and (3) the ``alert:repo-not-profiled`` issue
# opener/closer. A thin CLI at the bottom composes them.
#
# Fleet filter (per Round 1 interview answer in §2):
#   * Owner: ``Prekzursil``.
#   * Visibility: public only, with one explicit exception —
#     ``Prekzursil/pbinfo-get-unsolved`` is private but MUST be governed.
#   * Forks and GitHub template repos are excluded.
#   * Archived repos stay in the fleet and carry their profile; the
#     profile can flag them read-only if appropriate.

from __future__ import absolute_import  # noqa: UP010 — required by codacy-compat test

import argparse
import json
import subprocess  # nosec B404 — gh CLI wrapper; all args are controlled
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
    # Private repos are excluded unless explicitly whitelisted.
    return not (private and slug not in PRIVATE_INCLUDE_SLUGS)


def build_expected_fleet(github_repos: Iterable[Mapping[str, Any]]) -> List[str]:
    """Return the sorted list of slugs the fleet filter says to govern."""
    expected: Set[str] = set()
    for repo in github_repos:
        if not _should_include_repo(repo):
            continue
        slug = _slug_from_github_repo(repo)
        if slug is not None:  # pragma: no branch — _should_include_repo guarantees slug
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


# ---------------------------------------------------------------------------
# CLI entrypoint. Ties fetch → diff → alerts into a single script invocation
# so workflows can call one command and get a machine-readable report plus a
# non-zero exit code on gaps.
# ---------------------------------------------------------------------------


def format_diff_report(diff: FleetDiff) -> str:
    """Human-readable summary of a FleetDiff — stable ordering for CI logs."""
    lines = [
        "Fleet inventory report",
        "======================",
        f"Expected fleet:   {len(diff.expected)} repos",
        f"Inventoried:      {len(diff.inventoried)} repos",
        f"Missing (gap):    {len(diff.missing)}",
        f"Dead (orphan):    {len(diff.dead)}",
    ]
    if diff.missing:
        lines.append("")
        lines.append("Missing from inventory:")
        lines.extend(f"  - {slug}" for slug in diff.missing)
    if diff.dead:
        lines.append("")
        lines.append("In inventory but not on GitHub:")
        lines.extend(f"  - {slug}" for slug in diff.dead)
    return "\n".join(lines) + "\n"


def run_inventory_sweep(
    *,
    owner: str,
    platform_slug: str,
    inventory_path: Path,
    open_alerts: bool = False,
    close_resolved: bool = False,
    dry_run: bool = False,
    runner: ProcessRunner = subprocess.run,
) -> FleetDiff:
    """Perform one end-to-end inventory sweep.

    1. Fetch public repos via ``gh api /users/{owner}/repos``.
    2. Fetch private repos visible to token via ``gh api /user/repos``.
       (A failure here is non-fatal — the public fetch already covers the
       fleet filter except for explicit private exceptions.)
    3. Apply fleet filter + diff against inventory file.
    4. Optionally open alert issues for missing slugs / close for resolved.

    Returns the computed FleetDiff so callers can render / exit-code.
    """
    public_repos = fetch_user_repos(owner, runner=runner)
    try:
        auth_repos = fetch_authenticated_repos(runner=runner)
    except subprocess.CalledProcessError:
        auth_repos = []

    combined = merge_repo_lists(public_repos, auth_repos)
    expected = build_expected_fleet(combined)
    inventoried = load_inventory_slugs(inventory_path)
    diff = diff_fleet(expected, inventoried)

    if open_alerts:
        for slug in diff.missing:
            open_alert_issue_for_unprofiled_repo(
                platform_slug,
                slug=slug,
                runner=runner,
                dry_run=dry_run,
            )

    if close_resolved:
        for slug in diff.inventoried:
            if slug in diff.dead:
                continue  # dead slug: inventory entry still present, skip
            close_alert_issue_for_profiled_repo(
                platform_slug,
                slug=slug,
                runner=runner,
                dry_run=dry_run,
            )

    return diff


def _build_arg_parser() -> argparse.ArgumentParser:
    """Expose the CLI surface so tests can parse-and-inspect cleanly."""
    parser = argparse.ArgumentParser(
        description=(
            "Fleet inventory sweep: compare the GitHub fleet filter to "
            "inventory/repos.yml and optionally open alert:repo-not-profiled "
            "issues for gaps."
        ),
    )
    parser.add_argument("--owner", default="Prekzursil")
    parser.add_argument(
        "--platform-slug", default="Prekzursil/quality-zero-platform"
    )
    parser.add_argument(
        "--inventory",
        default=str(Path(__file__).resolve().parents[2] / "inventory" / "repos.yml"),
    )
    parser.add_argument(
        "--open-alerts",
        action="store_true",
        help="Open an alert:repo-not-profiled issue for each missing slug.",
    )
    parser.add_argument(
        "--close-resolved",
        action="store_true",
        help="Close any alert:repo-not-profiled issue that no longer applies.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip gh write operations; print what would happen.",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit JSON instead of the human-readable report.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint.

    Exit codes:
        0 — fleet matches inventory (no gaps, no dead entries)
        1 — gaps detected (non-fatal; useful for report-only CI step)
        2 — error surfaced while talking to ``gh``
    """
    args = _build_arg_parser().parse_args(argv)
    try:
        diff = run_inventory_sweep(
            owner=args.owner,
            platform_slug=args.platform_slug,
            inventory_path=Path(args.inventory),
            open_alerts=args.open_alerts,
            close_resolved=args.close_resolved,
            dry_run=args.dry_run,
        )
    except subprocess.CalledProcessError as exc:
        print(
            f"fleet_inventory: gh call failed (exit={exc.returncode}):\n"
            f"{exc.stderr}",
            flush=True,
        )
        return 2

    if args.json_output:
        print(
            json.dumps(
                {
                    "expected": diff.expected,
                    "inventoried": diff.inventoried,
                    "missing": diff.missing,
                    "dead": diff.dead,
                },
                indent=2,
            ),
            flush=True,
        )
    else:
        print(format_diff_report(diff), end="", flush=True)

    return 0 if not diff.missing and not diff.dead else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
