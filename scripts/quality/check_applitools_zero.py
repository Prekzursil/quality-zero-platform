#!/usr/bin/env python3
"""Gate script: assert zero Applitools visual regressions (per §9.4).

Usage:
    python scripts/quality/check_applitools_zero.py --json results.json [--out-json out.json] [--out-md out.md]

Checks: unresolved == 0 AND failed == 0. Exits 0 on pass, 1 on fail.
"""
from __future__ import absolute_import

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple


def _check_applitools(data: dict) -> Tuple[bool, int, int, int]:
    """Check Applitools stepsInfo. Returns (passed, total, unresolved, failed)."""
    steps = data.get("stepsInfo", {})
    if not isinstance(steps, dict):
        return (False, 0, 0, 0)

    total = int(steps.get("total", 0))
    unresolved = int(steps.get("unresolved", 0))
    failed = int(steps.get("failed", 0))

    passed = unresolved == 0 and failed == 0
    return (passed, total, unresolved, failed)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Applitools zero-regression gate")
    parser.add_argument("--json", required=True, dest="json_file", help="Path to Applitools JSON output")
    parser.add_argument("--out-json", default=None, help="Write JSON summary")
    parser.add_argument("--out-md", default=None, help="Write Markdown summary")
    args = parser.parse_args(argv)

    json_path = Path(args.json_file)
    if not json_path.exists():
        print(f"Applitools JSON not found: {json_path}", file=sys.stderr)
        return 1

    data = json.loads(json_path.read_text(encoding="utf-8"))
    passed, total, unresolved, failed = _check_applitools(data)

    summary = {
        "provider": "Applitools",
        "total_steps": total,
        "unresolved": unresolved,
        "failed": failed,
        "pass": passed,
    }

    if args.out_json:
        out_json = Path(args.out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if args.out_md:
        status = "PASS" if passed else "FAIL"
        md = (
            f"# Applitools Zero\n\n**Status:** {status}\n**Total:** {total}\n"
            f"**Unresolved:** {unresolved}\n**Failed:** {failed}\n"
        )
        out_md = Path(args.out_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(md, encoding="utf-8")

    if not passed:
        print(f"Applitools zero gate FAILED: {unresolved} unresolved, {failed} failed.", file=sys.stderr)
        return 1

    print("Applitools zero gate passed: no visual regressions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
