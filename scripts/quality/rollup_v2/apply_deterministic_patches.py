#!/usr/bin/env python3
"""Auto-apply deterministic patches from canonical.json (per design §5.2).

Reads canonical.json, filters findings with patch_source == "deterministic"
and a non-null patch, and applies each via ``git apply``.  Findings whose
patches conflict are recorded as skipped.
"""
from __future__ import absolute_import

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Auto-apply deterministic patches from canonical.json."
    )
    parser.add_argument(
        "--canonical-json",
        required=True,
        help="Path to canonical.json produced by rollup_v2.",
    )
    parser.add_argument(
        "--repo-dir",
        required=True,
        help="Path to the repository working tree.",
    )
    parser.add_argument(
        "--out-json",
        required=True,
        help="Path to write the result JSON (applied/skipped counts).",
    )
    return parser.parse_args()


def filter_patchable_findings(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return only findings with patch_source == 'deterministic' and a non-null patch."""
    return [
        f
        for f in findings
        if f.get("patch_source") == "deterministic" and f.get("patch") is not None
    ]


def apply_single_patch(diff: str, repo_dir: Path) -> Dict[str, Any]:
    """Try to apply a single unified diff via ``git apply``.

    Returns a dict with ``applied`` (bool), ``skipped`` (bool), and
    optionally ``reason`` (str) when skipped.
    """
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".patch",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write(diff)
        tmp_path = Path(tmp.name)

    try:
        # Dry-run first
        check_result = subprocess.run(
            ["git", "apply", "--check", str(tmp_path)],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        if check_result.returncode != 0:
            return {
                "applied": False,
                "skipped": True,
                "reason": check_result.stderr.strip() or "git apply --check failed",
            }

        # Apply for real
        apply_result = subprocess.run(
            ["git", "apply", str(tmp_path)],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        if apply_result.returncode != 0:
            return {
                "applied": False,
                "skipped": True,
                "reason": apply_result.stderr.strip() or "git apply failed",
            }

        return {"applied": True, "skipped": False}
    finally:
        tmp_path.unlink(missing_ok=True)


def run_patcher(
    canonical: Dict[str, Any],
    repo_dir: Path,
) -> Dict[str, Any]:
    """Run the full patcher: filter, apply, record results.

    Returns a dict with applied/skipped lists and counts.
    """
    findings = canonical.get("findings", [])
    patchable = filter_patchable_findings(findings)

    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for finding in patchable:
        result = apply_single_patch(finding["patch"], repo_dir)
        entry = {
            "finding_id": finding.get("finding_id", ""),
            "file": finding.get("file", ""),
            "category": finding.get("category", ""),
        }
        if result["applied"]:
            applied.append(entry)
        else:
            entry["reason"] = result.get("reason", "unknown")
            skipped.append(entry)

    return {
        "applied": applied,
        "skipped": skipped,
        "applied_count": len(applied),
        "skipped_count": len(skipped),
    }


def main() -> int:
    """CLI entry point."""
    args = parse_args()
    canonical_path = Path(args.canonical_json)
    repo_dir = Path(args.repo_dir)

    # Inline path-traversal sanitisation (Sonar pythonsecurity:S2083).
    # Sonar's taint analyser doesn't follow ``safe_output_path()`` from
    # ``scripts.quality.common`` inter-procedurally, so the validation
    # is performed inline here using the explicit ``str().startswith()``
    # pattern Sonar recognises as a containment check. Resolves the raw
    # arg against ``Path.cwd()`` (Python's ``Path.__truediv__`` discards
    # the left operand when the right operand is absolute, so this
    # single expression handles both relative and absolute inputs), then
    # rejects anything that escapes that root.
    workspace_root = Path.cwd().resolve()
    out_path = (workspace_root / Path(args.out_json)).resolve(strict=False)
    workspace_root_str = str(workspace_root)
    out_path_str = str(out_path)
    if not out_path_str.startswith(workspace_root_str + os.sep):
        raise ValueError(
            f"--out-json escapes workspace root: {out_path_str}",
        )

    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    result = run_patcher(canonical, repo_dir)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result, indent=2) + "\n",
        encoding="utf-8",
    )

    return 0


if __name__ == "__main__":  # pragma: no cover -- script entrypoint
    raise SystemExit(main())
