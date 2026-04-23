#!/usr/bin/env python3
"""Detect drift between a consumer repo and the rendered platform templates.

Phase 3 of ``docs/QZP-V2-DESIGN.md`` §4 ships per-stack templates that
the drift-sync workflow renders into consumer repos. This module is
the core comparator the workflow invokes before opening a PR:

1. Load the resolved profile JSON (output of ``export_profile.py``).
2. For every template in ``list_templates(profile['stack'])`` render
   it against the profile as context.
3. For every rendered output compare against the consumer repo's
   actual file. Report one of three statuses:

   * ``missing``   — template would create a new file.
   * ``drift``     — file exists but the body that would render differs
                     from the current bytes.
   * ``in_sync``   — file exists and matches the rendered body byte-for-byte.

4. Emit a unified diff for every ``drift`` + ``missing`` entry so the
   workflow can stage a PR.

Intentionally does NOT open PRs itself — that's the workflow's job so
this module stays testable without a GitHub credential.
"""

from __future__ import absolute_import

import argparse
import difflib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()

from scripts.quality.template_render import (  # noqa: E402
    list_templates,
    render_template,
)


@dataclass(frozen=True)
class DriftEntry:
    """One per-file drift result."""

    template_path: str
    output_path: str
    status: str  # "missing" | "drift" | "in_sync"
    diff: str  # unified diff text; empty for in_sync
    proposed_content: str


def _unified_diff(
    actual: str, proposed: str, output_path: str
) -> str:
    """Return a standard unified diff text between ``actual`` and ``proposed``."""
    return "".join(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=f"a/{output_path}",
            tofile=f"b/{output_path}",
        )
    )


def _classify_entry(
    proposed: str, current_path: Path, output_path: str
) -> DriftEntry:
    """Compute the drift status for a single template->output mapping."""
    if not current_path.is_file():
        return DriftEntry(
            template_path="",  # filled in by caller
            output_path=output_path,
            status="missing",
            diff=_unified_diff("", proposed, output_path),
            proposed_content=proposed,
        )
    actual = current_path.read_text(encoding="utf-8")
    if actual == proposed:
        return DriftEntry(
            template_path="",
            output_path=output_path,
            status="in_sync",
            diff="",
            proposed_content=proposed,
        )
    return DriftEntry(
        template_path="",
        output_path=output_path,
        status="drift",
        diff=_unified_diff(actual, proposed, output_path),
        proposed_content=proposed,
    )


def detect_drift(
    profile: Mapping[str, Any], repo_root: Path
) -> List[DriftEntry]:
    """Return a ``DriftEntry`` for each template that belongs to this stack.

    ``profile`` is the resolved profile JSON; ``profile['stack']`` selects
    which stack-specific templates to render. ``repo_root`` is the path to
    the checked-out consumer repo.
    """
    stack = str(profile.get("stack", "")).strip()
    if not stack:
        return []
    mapping = list_templates(stack)
    entries: List[DriftEntry] = []
    for template_path, output_path in sorted(mapping.items()):
        proposed = render_template(template_path, profile)
        entry = _classify_entry(proposed, repo_root / output_path, output_path)
        entries.append(
            DriftEntry(
                template_path=template_path,
                output_path=entry.output_path,
                status=entry.status,
                diff=entry.diff,
                proposed_content=entry.proposed_content,
            )
        )
    return entries


def drift_summary(entries: Iterable[DriftEntry]) -> Dict[str, int]:
    """Return a ``{status: count}`` dict. Useful for CI rollup output."""
    summary: Dict[str, int] = {"missing": 0, "drift": 0, "in_sync": 0}
    for entry in entries:
        summary[entry.status] = summary.get(entry.status, 0) + 1
    return summary


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile-json", required=True,
        help="Path to the resolved profile JSON (output of export_profile.py).",
    )
    parser.add_argument(
        "--repo-root", required=True,
        help="Path to the consumer repo's checkout.",
    )
    parser.add_argument(
        "--out-json", default="",
        help="Where to write the drift report JSON (stdout if empty).",
    )
    parser.add_argument(
        "--fail-on-drift", action="store_true",
        help="Exit non-zero when drift or missing entries are detected.",
    )
    return parser.parse_args()


def _build_report(entries: Iterable[DriftEntry]) -> Dict[str, Any]:
    """Build the JSON report that downstream tooling consumes."""
    entry_list = list(entries)
    return {
        "summary": drift_summary(entry_list),
        "entries": [
            {
                "template_path": e.template_path,
                "output_path": e.output_path,
                "status": e.status,
                "diff": e.diff,
                "proposed_content": e.proposed_content,
            }
            for e in entry_list
        ],
    }


def main() -> int:
    """CLI entrypoint."""
    args = _parse_args()
    profile_path = Path(args.profile_json)
    if not profile_path.is_file():
        print(
            f"drift_sync: profile JSON not found: {profile_path}",
            file=sys.stderr, flush=True,
        )
        return 2
    repo_root = Path(args.repo_root)
    if not repo_root.is_dir():
        print(
            f"drift_sync: repo root not found: {repo_root}",
            file=sys.stderr, flush=True,
        )
        return 2
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    entries = detect_drift(profile, repo_root)
    report = _build_report(entries)
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.out_json:
        Path(args.out_json).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    summary = report["summary"]
    out_of_sync = summary.get("missing", 0) + summary.get("drift", 0)
    if args.fail_on_drift and out_of_sync:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
