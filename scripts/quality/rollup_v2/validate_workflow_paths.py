#!/usr/bin/env python3
"""Validate workflow-supplied paths stay within the repo root (per §B.2.2).

Standalone script invoked by reusable workflows to ensure user-controlled
path inputs (``eyes_config_path``, ``storybook_dir``, etc.) cannot escape
the repository root via traversal or symlink tricks.

Usage:
    python scripts/quality/rollup_v2/validate_workflow_paths.py --repo-root /path --path storybook-static
    python scripts/quality/rollup_v2/validate_workflow_paths.py --repo-root /path --path applitools.config.js
"""
from __future__ import absolute_import

import argparse
import sys
from pathlib import Path
from typing import List

from scripts.quality.rollup_v2.path_safety import PathEscapedRootError, validate_finding_file


def validate_paths(repo_root: Path, paths: List[str]) -> List[str]:
    """Validate a list of paths against repo_root.

    Returns a list of error messages (empty if all paths are valid).
    """
    errors: List[str] = []
    for p in paths:
        try:
            validate_finding_file(p, repo_root)
        except PathEscapedRootError as exc:
            errors.append(f"Path validation failed for {p!r}: {exc}")
    return errors


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate workflow-supplied paths against repo root (§B.2.2)"
    )
    parser.add_argument("--repo-root", required=True, help="Repository root directory")
    parser.add_argument("--path", action="append", dest="paths", required=True,
                        help="Path to validate (can be repeated)")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    errors = validate_paths(repo_root, args.paths)

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print(f"All {len(args.paths)} path(s) validated successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
