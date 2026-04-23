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

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Set

import yaml  # type: ignore[import-untyped]


# The canonical list of private-repo exceptions. Kept tiny and explicit
# so adding a new private repo to the fleet requires a code change + PR,
# rather than a silent data-only edit.
PRIVATE_INCLUDE_SLUGS: frozenset[str] = frozenset(
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
