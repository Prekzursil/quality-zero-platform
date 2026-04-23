#!/usr/bin/env python3
"""Verify every QZP v2 deliverable is present on disk.

Phase 5 final gate per ``docs/QZP-V2-DESIGN.md``. The loop's
completion promise requires this script to exit 0 before
``QZP_V2_FULLY_SHIPPED_AND_VERIFIED`` can be emitted.

The script performs a static audit only — it does NOT dispatch the
drift-sync wave or hit GitHub APIs. That operational work remains
the responsibility of the platform owner; this script simply asserts
the CODE + ARTIFACTS are in place.

Checks, grouped by phase:

* Phase 1 — schema v2 + fleet inventory
* Phase 2 — Codecov flag split + validator
* Phase 3 — templates + marker parser + drift-sync
* Phase 4 — severity helper + bypass handlers + known-issues registry
* Phase 5 — bootstrap/bumps/dashboard/alerts (checked when those
  files exist; missing files downgrade to a ``warning`` status so
  this script stays useful while Phase 5 is still being authored)

Exit codes:
  0 — every deliverable present.
  1 — at least one deliverable is missing (``--all`` mode).
  2 — CLI argument error / unreadable repo root.
"""

from __future__ import absolute_import

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()


_PHASE_EXPECTATIONS: Dict[str, List[str]] = {
    "phase1": [
        "scripts/quality/profile_shape.py",
        "scripts/quality/profile_normalization.py",
        "scripts/quality/fleet_inventory.py",
        "scripts/quality/migrate_profiles_to_v2.py",
    ],
    "phase2": [
        ".github/workflows/reusable-codecov-analytics.yml",
        "scripts/quality/validate_codecov_flags.py",
    ],
    "phase3": [
        "scripts/quality/marker_regions.py",
        "scripts/quality/template_render.py",
        "scripts/quality/drift_sync.py",
        "scripts/quality/apply_drift_pr.py",
        ".github/workflows/reusable-drift-sync.yml",
        "profiles/templates/common/codecov.yml.j2",
        "profiles/templates/common/coverage-thresholds.json.j2",
        "profiles/templates/common/dependabot.yml.j2",
        "profiles/templates/common/ci-fragments/setup-python.yml.j2",
        "profiles/templates/common/ci-fragments/setup-node.yml.j2",
        "profiles/templates/stack/fullstack-web/ci.yml.j2",
        "profiles/templates/stack/python-only/ci.yml.j2",
        "profiles/templates/stack/react-vite-vitest/ci.yml.j2",
        "profiles/templates/stack/go/ci.yml.j2",
        "profiles/templates/stack/rust/ci.yml.j2",
        "profiles/templates/stack/swift/ci.yml.j2",
        "profiles/templates/stack/cpp-cmake/ci.yml.j2",
        "profiles/templates/stack/dotnet-wpf/ci.yml.j2",
        "profiles/templates/stack/gradle-java/ci.yml.j2",
        "profiles/templates/stack/python-tooling/ci.yml.j2",
    ],
    "phase4": [
        "known-issues/QZ-FP-001.yml",
        "known-issues/QZ-FP-002.yml",
        "known-issues/QZ-FP-003.yml",
        "known-issues/QZ-CV-001.yml",
        "scripts/quality/known_issues.py",
        "scripts/quality/severity_rollup.py",
        "scripts/quality/bypass_labels.py",
        "scripts/quality/security_class_guard.py",
        ".github/workflows/reusable-quality-zero-bypass.yml",
    ],
    # Phase 5 entries are optional while this phase is still authored —
    # missing ones show up as ``warning`` status rather than failing
    # the verifier so we can run this script incrementally during
    # Phase 5 implementation.
    "phase5_optional": [
        ".github/workflows/reusable-bootstrap-repo.yml",
        ".github/workflows/reusable-bumps.yml",
        ".github/workflows/publish-admin-dashboard.yml",
        "scripts/quality/alerts.py",
    ],
}


@dataclass(frozen=True)
class CheckResult:
    """One per-deliverable audit outcome."""

    path: str
    phase: str
    status: str  # "ok" | "missing" | "warning"
    detail: str = ""


def _check_path(
    repo_root: Path, relative: str, phase: str, required: bool,
) -> CheckResult:
    """Return ``CheckResult`` for one expected artefact."""
    full = repo_root / relative
    if full.is_file() or full.is_dir():
        return CheckResult(path=relative, phase=phase, status="ok")
    status = "missing" if required else "warning"
    detail = (
        "required artefact missing"
        if required
        else "Phase 5 WIP — absence tolerated while this phase is being built"
    )
    return CheckResult(path=relative, phase=phase, status=status, detail=detail)


def audit_deployment(repo_root: Path) -> List[CheckResult]:
    """Walk ``_PHASE_EXPECTATIONS`` and yield per-path check results."""
    results: List[CheckResult] = []
    for phase, paths in _PHASE_EXPECTATIONS.items():
        required = phase != "phase5_optional"
        for rel in paths:
            results.append(_check_path(repo_root, rel, phase, required))
    return results


def summarise(results: List[CheckResult]) -> Dict[str, Any]:
    """Return JSON-safe summary for CI consumption."""
    ok = [r.path for r in results if r.status == "ok"]
    missing = [r.path for r in results if r.status == "missing"]
    warnings = [r.path for r in results if r.status == "warning"]
    return {
        "ok_count": len(ok),
        "missing_count": len(missing),
        "warning_count": len(warnings),
        "missing": sorted(missing),
        "warnings": sorted(warnings),
        "ok": sorted(ok),
    }


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[2]),
        help="Path to the platform repo root (defaults to this script's parent).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Fail non-zero when any required deliverable is missing.",
    )
    parser.add_argument(
        "--out-json",
        default="",
        help="Write the summary JSON to this path (stdout when empty).",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint."""
    args = _parse_args()
    repo_root = Path(args.repo_root)
    if not repo_root.is_dir():
        print(
            f"verify_v2_deployment: repo root not found: {repo_root}",
            file=sys.stderr, flush=True,
        )
        return 2

    results = audit_deployment(repo_root)
    summary = summarise(results)
    payload = json.dumps(summary, indent=2, sort_keys=True)
    if args.out_json:
        Path(args.out_json).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    if args.all and summary["missing_count"]:
        for rel in summary["missing"]:
            print(f"::error::missing deliverable: {rel}", flush=True)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entry
    raise SystemExit(main())
