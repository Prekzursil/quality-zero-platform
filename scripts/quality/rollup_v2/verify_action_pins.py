#!/usr/bin/env python3
"""Verify all third-party GitHub Actions are SHA-pinned (per §B.3.6 §A.2.4).

Scans ``.github/workflows/*.yml`` for ``uses:`` directives and fails if any
third-party action uses a floating tag (e.g., ``@v4``) instead of a SHA pin.

First-party ``actions/*`` are exempted per §A.2.4.

Usage:
    python scripts/quality/rollup_v2/verify_action_pins.py --workflows-dir .github/workflows
    python scripts/quality/rollup_v2/verify_action_pins.py --workflows-dir .github/workflows --strict
"""
from __future__ import absolute_import

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List

# First-party / GitHub-maintained action owners exempt from SHA pinning.
# actions/* = GitHub first-party (checkout, setup-python, upload-artifact, etc.)
# github/* = GitHub-maintained tools (codeql-action, super-linter, etc.)
_EXEMPT_OWNERS: frozenset[str] = frozenset({"actions", "github"})

# Regex to match `uses: owner/repo@ref` lines in workflow YAML
_USES_RE = re.compile(
    r"uses:\s*(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)(?:/[A-Za-z0-9_./%-]+)?@(?P<ref>\S+)"
)

# SHA pattern: 40 hex chars
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def scan_workflow(path: Path) -> List[Dict[str, str]]:
    """Scan a single workflow file for floating-tag action references.

    Returns a list of violation dicts with keys: file, line, action, ref.
    """
    violations: List[Dict[str, str]] = []
    text = path.read_text(encoding="utf-8")

    for line_num, line in enumerate(text.splitlines(), start=1):
        match = _USES_RE.search(line)
        if match is None:
            continue

        owner = match.group("owner")
        repo = match.group("repo")
        ref = match.group("ref")

        # Strip inline comments from ref (e.g., `@abc123  # v4`)
        ref = ref.split("#")[0].strip()

        # Exempt first-party actions
        if owner.lower() in _EXEMPT_OWNERS:
            continue

        # Check if ref is a SHA pin
        if _SHA_RE.match(ref):
            continue

        violations.append({
            "file": str(path),
            "line": str(line_num),
            "action": f"{owner}/{repo}",
            "ref": ref,
        })

    return violations


def scan_workflows_dir(workflows_dir: Path) -> List[Dict[str, str]]:
    """Scan all YAML files in a workflows directory."""
    all_violations: List[Dict[str, str]] = []
    if not workflows_dir.is_dir():
        return all_violations

    for yml_path in sorted(workflows_dir.glob("*.yml")):
        all_violations.extend(scan_workflow(yml_path))

    for yaml_path in sorted(workflows_dir.glob("*.yaml")):
        all_violations.extend(scan_workflow(yaml_path))

    return all_violations


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify third-party GitHub Actions are SHA-pinned (§B.3.6)"
    )
    parser.add_argument(
        "--workflows-dir",
        required=True,
        help="Path to .github/workflows directory",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Exit non-zero on any violation (default behavior)",
    )
    args = parser.parse_args(argv)

    workflows_dir = Path(args.workflows_dir)
    violations = scan_workflows_dir(workflows_dir)

    if violations:
        print(f"Found {len(violations)} floating-tag action reference(s):", file=sys.stderr)
        for v in violations:
            print(f"  {v['file']}:{v['line']} — {v['action']}@{v['ref']}", file=sys.stderr)
        return 1

    yml_count = len(list(workflows_dir.glob("*.yml"))) + len(list(workflows_dir.glob("*.yaml")))
    print(f"All third-party actions are SHA-pinned ({yml_count} workflow files scanned).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
