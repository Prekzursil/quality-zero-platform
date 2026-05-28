"""Migrate ``profiles/repos/<slug>.yml`` files to schema v2 in-place."""
# The migration is idempotent: re-running it on an already-v2 profile
# produces the same output. The script is thin by design — all derivation
# rules live in :mod:`scripts.quality.profile_normalization`, which also
# powers the load-time auto-migration path. Having the migration emit
# those same values into the on-disk YAML means downstream tooling sees
# explicit v2 fields instead of having to re-derive them every load.
#
# Rules:
#   * Adds ``version: 2`` near the top of the file if absent.
#   * Synthesises ``mode`` from legacy ``issue_policy.mode`` when absent.
#   * Synthesises ``scanners`` from legacy ``enabled_scanners`` (every
#     true-valued scanner maps to ``severity: block``, matching the strict
#     default in docs/QZP-V2-DESIGN.md §10.1).
#   * Adds ``flag`` to each ``coverage.inputs`` item when missing, using
#     the item's ``name`` — this is the single most important migration
#     because the reusable-codecov-analytics loop keys off ``flag`` for
#     per-upload split (fixes the event-link 58% ghost-coverage bug).
#   * Adds ``overrides: []`` if absent so drift-sync has a deterministic
#     field to compare against.
#   * Does NOT delete legacy ``issue_policy`` / ``enabled_scanners``.
#     They stay during the migration window so consumers still reading
#     those keys directly keep working; Phase 4 will remove them once
#     every reader has switched to the v2 shape.

from __future__ import absolute_import  # noqa: UP010 — required by codacy-compat test

import argparse
import sys
from pathlib import Path
from typing import Iterable, List

if str(Path(__file__).resolve().parents[2]) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import yaml  # type: ignore[import-untyped]

from scripts.quality.migrate_profiles_to_v2_rules import (  # noqa: F401  pylint: disable=unused-import
    migrate_profile,
)

PROFILES_DIR_DEFAULT = Path(__file__).resolve().parents[2] / "profiles" / "repos"


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
    """Construct the CLI argument parser (extracted for testability)."""
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
    """CLI entrypoint. Returns 0 on success, 2 when the target dir is absent."""
    args = _build_arg_parser().parse_args(argv)
    profiles_dir = Path(args.profiles_dir)
    if not profiles_dir.is_dir():
        print(f"migrate_profiles_to_v2: no such dir: {profiles_dir}", flush=True)
        return 2

    changed = _migrate_all(profiles_dir, dry_run=args.dry_run)
    _print_summary(changed, dry_run=args.dry_run)
    return 0


def _migrate_all(profiles_dir: Path, *, dry_run: bool) -> List[Path]:
    """Iterate every profile under ``profiles_dir`` and migrate or skip it."""
    changed: List[Path] = []
    for path in _iter_profile_paths(profiles_dir):
        if _migrate_one(path, dry_run=dry_run):
            changed.append(path)
    return changed


def _migrate_one(path: Path, *, dry_run: bool) -> bool:
    """Return ``True`` when the file needs (or would need) a v2 migration."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return False
    migrated = migrate_profile(raw)
    if migrated == raw:
        return False
    if dry_run:
        print(f"would migrate: {path.name}", flush=True)
        return True
    path.write_text(
        yaml.safe_dump(migrated, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    print(f"migrated: {path.name}", flush=True)
    return True


def _print_summary(changed: List[Path], *, dry_run: bool) -> None:
    """Emit the closing summary line for ``main``."""
    if not changed:
        print("No profiles required migration.", flush=True)
        return
    verb = "would change" if dry_run else "migrated"
    print(f"{len(changed)} profile(s) {verb}.", flush=True)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
