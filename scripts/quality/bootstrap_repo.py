#!/usr/bin/env python3
"""Phase 5 shadow-mode bootstrap primitives.

Powers ``reusable-bootstrap-repo.yml`` — the workflow that onboards a new
consumer repo via 3-consecutive-green-shadow-runs before flipping the
``mode.phase`` from ``shadow`` → ``absolute`` (or ``ratchet``).

Primitives:

* ``count_consecutive_green_shadow_runs(slug, workflow, branch, runner)``
  — walks ``gh run list`` newest-first and counts contiguous
  ``conclusion == "success"`` runs. ``in_progress`` runs are skipped
  (neither counted nor a stopper). A non-success completed run is the
  stopper.
* ``should_promote(green_run_count, required=3)`` — trivial gate
  predicate that keeps the 3-green threshold adjustable in tests +
  future policy work.
* ``promote_profile(profile_yaml, target_phase)`` — text-level rewriter
  that flips ``mode.phase: shadow`` → ``mode.phase: <target_phase>`` and
  rewrites ``shadow_until: <date>`` → ``shadow_until: null``. Byte-for-
  byte preserves every other line so downstream diffs are minimal.
* ``compute_promotion_plan(...)`` — convenience composition returning
  ``{"ready", "green_runs", "promoted_yaml"}`` for the workflow step.

The module is deliberately IO-free: callers pass ``ProcessRunner`` in to
run gh, and pass ``profile_yaml`` in as text (not a file path). This
keeps every function unit-testable without disk or network.
"""

from __future__ import absolute_import

import json
import re
import subprocess  # nosec B404 # noqa: S404 — gh CLI wrapper; args are controlled
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()


ProcessRunner = Callable[..., "subprocess.CompletedProcess[str]"]


ALLOWED_TARGET_PHASES = ("ratchet", "absolute")
DEFAULT_GREEN_RUN_THRESHOLD = 3


_PHASE_LINE_RE = re.compile(r"^(?P<indent>\s*)phase:\s*(?P<phase>\S+)\s*$")
_SHADOW_UNTIL_LINE_RE = re.compile(r"^(?P<indent>\s*)shadow_until:\s*.+$")


def _run_gh(
    args: List[str], *, runner: ProcessRunner,
) -> "subprocess.CompletedProcess[str]":
    """Invoke ``gh`` with shared defaults (capture, text, check=False)."""
    return runner(
        ["gh", *args], capture_output=True, text=True, check=False,
    )  # nosec B603 — gh args are controlled


def count_consecutive_green_shadow_runs(
    *,
    slug: str,
    workflow: str,
    branch: str,
    runner: ProcessRunner = subprocess.run,
    limit: int = 25,
) -> int:
    """Count contiguous newest-first green runs of ``workflow`` on ``branch``."""
    args = [
        "run", "list",
        "--repo", slug,
        "--workflow", workflow,
        "--branch", branch,
        "--limit", str(limit),
        "--json", "conclusion,status",
    ]
    completed = _run_gh(args, runner=runner)
    payload = json.loads(completed.stdout) if completed.stdout else []
    if not isinstance(payload, list):
        return 0
    consecutive = 0
    for run in payload:
        if not isinstance(run, dict):
            break
        status = str(run.get("status", ""))
        if status != "completed":
            # in_progress / queued runs neither count nor break the streak.
            continue
        if str(run.get("conclusion", "")) == "success":
            consecutive += 1
            continue
        break
    return consecutive


def should_promote(
    *, green_run_count: int, required: int = DEFAULT_GREEN_RUN_THRESHOLD,
) -> bool:
    """Return True iff ``green_run_count`` meets/exceeds ``required``."""
    return green_run_count >= required


def promote_profile(profile_yaml: str, *, target_phase: str) -> str:
    """Rewrite ``mode.phase`` and clear ``shadow_until`` in ``profile_yaml``."""
    if target_phase not in ALLOWED_TARGET_PHASES:
        raise ValueError(
            f"target_phase must be one of {ALLOWED_TARGET_PHASES}; "
            f"got {target_phase!r}",
        )
    rewritten_lines: List[str] = []
    found_shadow_phase = False
    for line in profile_yaml.splitlines(keepends=True):
        phase_match = _PHASE_LINE_RE.match(line.rstrip("\n").rstrip("\r"))
        if phase_match and phase_match.group("phase") == "shadow":
            found_shadow_phase = True
            indent = phase_match.group("indent")
            rewritten_lines.append(f"{indent}phase: {target_phase}\n")
            continue
        shadow_match = _SHADOW_UNTIL_LINE_RE.match(
            line.rstrip("\n").rstrip("\r"),
        )
        if shadow_match:
            indent = shadow_match.group("indent")
            rewritten_lines.append(f"{indent}shadow_until: null\n")
            continue
        rewritten_lines.append(line)
    if not found_shadow_phase:
        raise ValueError(
            "promote_profile requires an existing 'mode.phase: shadow' line; "
            "profile is already past shadow phase.",
        )
    return "".join(rewritten_lines)


def compute_promotion_plan(
    *,
    slug: str,
    workflow: str,
    branch: str,
    profile_yaml: str,
    target_phase: str,
    runner: ProcessRunner = subprocess.run,
    required_green: int = DEFAULT_GREEN_RUN_THRESHOLD,
) -> Dict[str, Any]:
    """Return ``{ready, green_runs, promoted_yaml}`` summary for the workflow."""
    green_runs = count_consecutive_green_shadow_runs(
        slug=slug, workflow=workflow, branch=branch, runner=runner,
    )
    ready = should_promote(
        green_run_count=green_runs, required=required_green,
    )
    promoted_yaml = (
        promote_profile(profile_yaml, target_phase=target_phase)
        if ready else ""
    )
    return {
        "ready": ready,
        "green_runs": green_runs,
        "promoted_yaml": promoted_yaml,
    }


if __name__ == "__main__":  # pragma: no cover — ad-hoc CLI
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--profile", required=True, help="Path to profile YAML")
    parser.add_argument("--target-phase", required=True, choices=list(ALLOWED_TARGET_PHASES))
    parser.add_argument("--required", type=int, default=DEFAULT_GREEN_RUN_THRESHOLD)
    parser.add_argument("--out", default="", help="Write promoted YAML here; stdout otherwise")
    _args = parser.parse_args()

    _profile_text = Path(_args.profile).read_text(encoding="utf-8")
    _plan = compute_promotion_plan(
        slug=_args.slug,
        workflow=_args.workflow,
        branch=_args.branch,
        profile_yaml=_profile_text,
        target_phase=_args.target_phase,
        required_green=_args.required,
    )
    print(json.dumps(
        {"ready": _plan["ready"], "green_runs": _plan["green_runs"]},
        indent=2, sort_keys=True,
    ))
    if _plan["ready"]:
        if _args.out:
            Path(_args.out).write_text(_plan["promoted_yaml"], encoding="utf-8")
        else:
            print(_plan["promoted_yaml"])
