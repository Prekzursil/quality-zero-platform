#!/usr/bin/env python3
"""Phase 5 bump rollout planner.

Pure-logic primitives the ``reusable-bumps.yml`` workflow uses to turn
a validated bump recipe (see :mod:`bumps`) into a concrete rollout
plan:

* ``plan_rollout(recipe, fleet)`` — returns
  ``{"staging": [...], "rollout": [...]}`` split by wave.
* ``classify_staging_outcome(staging_results)`` — returns
  ``{"proceed_to_rollout", "rollback_required", "failed_repos"}``.

Both functions are IO-free so they are trivially unit-testable
without gh / disk / network. The workflow does the side-effecting
work (opening PRs, reverting, opening ``alert:fleet-bump-fail``).
"""

from __future__ import absolute_import

import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()


def _dedupe_preserving_order(values: Iterable[str]) -> List[str]:
    """Return ``values`` deduped + stripped, preserving first-seen order."""
    seen: Dict[str, None] = {}
    for raw in values:
        cleaned = str(raw).strip()
        if cleaned and cleaned not in seen:
            seen[cleaned] = None
    return list(seen.keys())


def plan_rollout(
    *,
    recipe: Mapping[str, Any],
    fleet: Sequence[Mapping[str, Any]],
) -> Dict[str, List[str]]:
    """Split ``fleet`` into staging vs full-rollout lists for ``recipe``."""
    affected_stacks = {str(s).strip() for s in recipe.get("affects_stacks", [])}
    staging_slugs = set(
        _dedupe_preserving_order(recipe.get("staging_repos", [])),
    )
    staging_order = _dedupe_preserving_order(recipe.get("staging_repos", []))

    rollout: List[str] = []
    for entry in fleet:
        slug = str(entry.get("slug", "")).strip()
        stack = str(entry.get("stack", "")).strip()
        if not slug or stack not in affected_stacks:
            continue
        if slug in staging_slugs:
            continue
        rollout.append(slug)

    if not recipe.get("full_rollout_after_staging", True):
        rollout = []

    return {"staging": staging_order, "rollout": rollout}


def classify_staging_outcome(
    *, staging_results: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Decide rollout/rollback/wait from staging CI conclusions."""
    if not staging_results:
        return {
            "proceed_to_rollout": False,
            "rollback_required": False,
            "failed_repos": [],
        }
    failed = [
        str(entry.get("slug", "<unknown>"))
        for entry in staging_results
        if str(entry.get("conclusion", "")) != "success"
    ]
    return {
        "proceed_to_rollout": not failed,
        "rollback_required": bool(failed),
        "failed_repos": failed,
    }


if __name__ == "__main__":  # pragma: no cover — ad-hoc CLI
    import argparse
    import json

    from scripts.quality import bumps

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--fleet", default="")
    _args = parser.parse_args()

    _recipe = bumps.load_bump_recipe(Path(_args.recipe))
    _fleet: List[Dict[str, Any]] = []
    if _args.fleet:
        _fleet = json.loads(Path(_args.fleet).read_text(encoding="utf-8"))
    _plan = plan_rollout(recipe=_recipe, fleet=_fleet)
    print(json.dumps({
        "recipe_name": _recipe["name"],
        "staging": _plan["staging"],
        "rollout": _plan["rollout"],
    }, indent=2, sort_keys=True))
