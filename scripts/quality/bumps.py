#!/usr/bin/env python3
"""Phase 5 bump recipe primitives.

Bumps are platform-owned recipes that describe a fleet-wide nudge
(e.g. ``Node 20 → 24``, ``ubuntu-latest → ubuntu-24.04``). They live
in ``profiles/bumps/<date>-<name>.yml`` and are consumed by
``reusable-bumps.yml`` in a staged-rollout workflow:

1. Open PRs against ``staging_repos`` first. Wait for all CI green.
2. If all green → open PRs against the remainder of the affected
   fleet (every repo whose ``stack`` is in ``affects_stacks``).
3. If any staging PR fails CI → revert the bump recipe commit and
   open ``alert:fleet-bump-fail`` on the platform repo.

This module ships the **schema validator + file-resolver primitives**
only. The actual PR-opening + rollout orchestration is wired by the
workflow and a follow-up increment (Phase 5 inc-3.5).

Recipe schema (per ``docs/QZP-V2-DESIGN.md`` §5.5):

```yaml
name: Node 20 -> 24             # required — human-readable label
target:                         # required — non-empty list
  - file_glob: "**/ci.yml"      # required per entry — glob relative to repo
    yaml_path: "jobs.*.steps..."  # required per entry — YAML selector (applier spec)
    value: '24'                 # required per entry — new value
affects_stacks:                 # required — non-empty list of stack ids
  - fullstack-web
staging_repos:                  # required — non-empty list of owner/name
  - Prekzursil/env-inspector
full_rollout_after_staging: true   # optional, defaults to True
rollback_on_failure: true          # optional, defaults to True
```
"""

from __future__ import absolute_import

import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

import yaml  # type: ignore[import-untyped]


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()


REQUIRED_TOP_FIELDS = (
    "name", "target", "affects_stacks", "staging_repos",
)
REQUIRED_TARGET_FIELDS = ("file_glob", "yaml_path", "value")


class BumpRecipeError(ValueError):
    """Raised when a bump recipe fails schema validation."""


def _validate_top_fields(recipe: Mapping[str, Any], path: Path) -> None:
    """Assert every required top-level field is present + non-empty."""
    for field in REQUIRED_TOP_FIELDS:
        if field not in recipe:
            raise BumpRecipeError(
                f"bump recipe {path} missing required field: {field!r}",
            )
    for list_field in ("target", "affects_stacks", "staging_repos"):
        value = recipe.get(list_field)
        if not isinstance(value, list) or not value:
            raise BumpRecipeError(
                f"bump recipe {path} field {list_field!r} must be a "
                f"non-empty list (got {type(value).__name__})",
            )


def _validate_target_entries(recipe: Mapping[str, Any], path: Path) -> None:
    """Assert every target entry has file_glob/yaml_path/value."""
    for index, entry in enumerate(recipe["target"]):
        if not isinstance(entry, Mapping):
            raise BumpRecipeError(
                f"bump recipe {path} target[{index}] must be a mapping",
            )
        for field in REQUIRED_TARGET_FIELDS:
            if field not in entry:
                raise BumpRecipeError(
                    f"bump recipe {path} target[{index}] missing {field!r}",
                )


def _validate_staging_repo_slugs(recipe: Mapping[str, Any], path: Path) -> None:
    """Assert each staging repo is an ``owner/name`` slug."""
    for slug in recipe["staging_repos"]:
        if not isinstance(slug, str) or "/" not in slug:
            raise BumpRecipeError(
                f"bump recipe {path} staging repo {slug!r} must be "
                f"'owner/name' format",
            )


def load_bump_recipe(path: Path) -> Dict[str, Any]:
    """Load, validate, and normalise a bump recipe YAML at ``path``.

    Returns a dict with all fields set (defaults applied). Raises
    ``BumpRecipeError`` on any schema violation.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise BumpRecipeError(
            f"bump recipe {path} must be a YAML mapping at top level",
        )
    _validate_top_fields(raw, path)
    _validate_target_entries(raw, path)
    _validate_staging_repo_slugs(raw, path)
    recipe: Dict[str, Any] = dict(raw)
    recipe.setdefault("full_rollout_after_staging", True)
    recipe.setdefault("rollback_on_failure", True)
    return recipe


def resolve_target_files(
    repo_root: Path, targets: Iterable[Mapping[str, Any]],
) -> List[Path]:
    """Expand every ``target[].file_glob`` against ``repo_root``.

    Returns the deduplicated, sorted list of files that match at
    least one target's glob. The glob syntax is ``pathlib.Path.glob``
    (``**`` recurses, ``*`` matches one path segment).
    """
    seen: Dict[str, Path] = {}
    for target in targets:
        glob = str(target["file_glob"])
        for hit in repo_root.glob(glob):
            if hit.is_file():
                key = hit.relative_to(repo_root).as_posix()
                seen.setdefault(key, hit)
    return [seen[k] for k in sorted(seen)]


if __name__ == "__main__":  # pragma: no cover — ad-hoc CLI
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", required=True, help="Path to bump recipe YAML")
    parser.add_argument("--repo-root", default=".", help="Resolve target globs here")
    _args = parser.parse_args()

    _recipe = load_bump_recipe(Path(_args.recipe))
    _files = resolve_target_files(
        Path(_args.repo_root), _recipe["target"],
    )
    print(json.dumps({
        "recipe_name": _recipe["name"],
        "staging_repos": _recipe["staging_repos"],
        "affects_stacks": _recipe["affects_stacks"],
        "target_files": [p.as_posix() for p in _files],
    }, indent=2, sort_keys=True))
