"""Migrate ``profiles/repos/<slug>.yml`` files to schema v2 in-place.

The migration is idempotent: re-running it on an already-v2 profile
produces the same output. The script is thin by design — all derivation
rules live in :mod:`scripts.quality.profile_normalization`, which also
powers the load-time auto-migration path. Having the migration emit
those same values into the on-disk YAML means downstream tooling sees
explicit v2 fields instead of having to re-derive them every load.

Rules:

* Adds ``version: 2`` near the top of the file if absent.
* Synthesises ``mode`` from legacy ``issue_policy.mode`` when absent.
* Synthesises ``scanners`` from legacy ``enabled_scanners`` (every
  true-valued scanner maps to ``severity: block``, matching the strict
  default in docs/QZP-V2-DESIGN.md §10.1).
* Adds ``flag`` to each ``coverage.inputs`` item when missing, using
  the item's ``name`` — this is the single most important migration
  because the reusable-codecov-analytics loop keys off ``flag`` for
  per-upload split (fixes the event-link 58% ghost-coverage bug).
* Adds ``overrides: []`` if absent so drift-sync has a deterministic
  field to compare against.
* Does NOT delete legacy ``issue_policy`` / ``enabled_scanners``.
  They stay during the migration window so consumers still reading
  those keys directly keep working; Phase 4 will remove them once
  every reader has switched to the v2 shape.
"""

from __future__ import absolute_import

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import yaml  # type: ignore[import-untyped]

from scripts.quality.profile_normalization import (
    normalize_mode,
    normalize_scanners,
)


PROFILES_DIR_DEFAULT = Path(__file__).resolve().parents[2] / "profiles" / "repos"


def migrate_profile(raw_profile: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a v2-migrated copy of ``raw_profile`` without mutating input.

    Keys already present at their v2 values are left untouched. Additions
    are inserted in a canonical order so the on-disk diff is small and
    reviewable.
    """
    migrated: Dict[str, Any] = dict(raw_profile)

    if "version" not in migrated:
        migrated["version"] = 2
    else:
        # Clamp unknown / v1 declarations to 2 — once this migrator touches
        # a file, the file is v2 by definition.
        try:
            declared = int(migrated["version"])
        except (TypeError, ValueError):
            declared = 1
        if declared < 2:
            migrated["version"] = 2

    if "mode" not in migrated:
        migrated["mode"] = _mode_block(migrated.get("issue_policy"))

    if "scanners" not in migrated:
        migrated["scanners"] = _scanners_block(migrated.get("enabled_scanners"))

    if "overrides" not in migrated:
        migrated["overrides"] = []

    coverage = migrated.get("coverage")
    if isinstance(coverage, Mapping):
        inputs = coverage.get("inputs")
        if isinstance(inputs, list):
            migrated_coverage = dict(coverage)
            migrated_coverage["inputs"] = _migrate_coverage_inputs(inputs)
            migrated["coverage"] = migrated_coverage

    return _reorder_for_review(migrated)


def _mode_block(legacy_issue_policy: Any) -> Dict[str, Any]:
    """Produce a tidy ``mode`` block for on-disk YAML.

    Uses the shared normaliser (same logic as the load-time path) and
    drops the ratchet sub-block when the phase isn't ratchet, so the
    YAML stays compact and readable.
    """
    result = normalize_mode(None, legacy_issue_policy=legacy_issue_policy)
    if result["phase"] != "ratchet":
        # Drop the placeholder ratchet + shadow_until fields for non-ratchet
        # repos so the YAML diff stays minimal. They can be re-added later
        # if the repo ever needs them.
        return {"phase": result["phase"]}
    return {
        "phase": result["phase"],
        "ratchet": result["ratchet"],
    }


def _scanners_block(legacy_enabled_scanners: Any) -> Dict[str, Dict[str, str]]:
    """Emit an explicit ``scanners`` map derived from legacy enabled flags."""
    return normalize_scanners(None, legacy_enabled_scanners=legacy_enabled_scanners)


def _migrate_coverage_inputs(inputs: List[Any]) -> List[Dict[str, Any]]:
    """Ensure every coverage input carries a ``flag`` (from ``name``)."""
    migrated: List[Dict[str, Any]] = []
    for item in inputs:
        if not isinstance(item, Mapping):
            migrated.append(item)  # type: ignore[arg-type]
            continue
        entry = dict(item)
        if "flag" not in entry or not str(entry.get("flag", "")).strip():
            entry["flag"] = str(entry.get("name", "")).strip()
        migrated.append(entry)
    return migrated


def _reorder_for_review(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Emit v2 fields near the top so YAML diffs land where a reviewer looks."""
    preferred_order = [
        "slug",
        "version",
        "stack",
        "mode",
        "scanners",
        "overrides",
    ]
    ordered: Dict[str, Any] = {}
    for key in preferred_order:
        if key in profile:
            ordered[key] = profile[key]
    for key, value in profile.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def _iter_profile_paths(profiles_dir: Path) -> Iterable[Path]:
    """Yield ``*.yml`` profile files in ``profiles_dir`` sorted by name."""
    return sorted(p for p in profiles_dir.glob("*.yml") if p.is_file())


def migrate_profile_file(path: Path) -> bool:
    """Migrate a single profile file on disk. Returns True when changed."""
    raw_text = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw_text) or {}
    if not isinstance(parsed, dict):
        return False
    migrated = migrate_profile(parsed)
    if migrated == parsed:
        return False
    path.write_text(
        yaml.safe_dump(migrated, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return True


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profiles-dir",
        default=str(PROFILES_DIR_DEFAULT),
        help="Directory holding profiles/repos/*.yml (default: repo-local).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing to disk.",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    profiles_dir = Path(args.profiles_dir)
    if not profiles_dir.is_dir():
        print(f"migrate_profiles_to_v2: no such dir: {profiles_dir}", flush=True)
        return 2

    changed: List[Path] = []
    for path in _iter_profile_paths(profiles_dir):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        migrated = migrate_profile(raw)
        if migrated == raw:
            continue
        if args.dry_run:
            print(f"would migrate: {path.name}", flush=True)
        else:
            path.write_text(
                yaml.safe_dump(migrated, sort_keys=False, default_flow_style=False),
                encoding="utf-8",
            )
            print(f"migrated: {path.name}", flush=True)
        changed.append(path)

    if not changed:
        print("No profiles required migration.", flush=True)
    else:
        print(f"{len(changed)} profile(s) {'would change' if args.dry_run else 'migrated'}.", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
