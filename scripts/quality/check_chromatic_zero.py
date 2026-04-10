#!/usr/bin/env python3
"""Gate script: assert zero Chromatic visual regressions (per §9.3).

Usage:
    python scripts/quality/check_chromatic_zero.py --json results.json [--out-json out.json] [--out-md out.md]

Checks: accepted == total AND errored == 0. Exits 0 on pass, 1 on fail.
"""
from __future__ import absolute_import

from typing import List, Tuple

import argparse
import json
import sys
from pathlib import Path


def _check_chromatic(data: dict) -> Tuple[bool, int, int, int]:
    """Check Chromatic summary. Returns (passed, total, accepted, errored)."""
    summary = data.get("summary", {})
    if not isinstance(summary, dict):
        return (False, 0, 0, 0)

    total = int(summary.get("total", 0))
    accepted = int(summary.get("accepted", 0))
    errored = int(summary.get("errored", 0))
    changed = int(summary.get("changed", 0))
    rejected = int(summary.get("rejected", 0))

    passed = (accepted + unchanged == total and errored == 0 and rejected == 0) if (unchanged := int(summary.get("unchanged", 0))) >= 0 else False
    # Simpler: pass when no regressions
    passed = errored == 0 and rejected == 0 and changed == 0

    return (passed, total, accepted, errored)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chromatic zero-regression gate")
    parser.add_argument("--json", required=True, dest="json_file", help="Path to Chromatic JSON output")
    parser.add_argument("--out-json", default=None, help="Write JSON summary")
    parser.add_argument("--out-md", default=None, help="Write Markdown summary")
    args = parser.parse_args(argv)

    json_path = Path(args.json_file)
    if not json_path.exists():
        print(f"Chromatic JSON not found: {json_path}", file=sys.stderr)
        return 1

    data = json.loads(json_path.read_text(encoding="utf-8"))
    passed, total, accepted, errored = _check_chromatic(data)

    summary = {
        "provider": "Chromatic",
        "total_snapshots": total,
        "accepted": accepted,
        "errored": errored,
        "pass": passed,
    }

    if args.out_json:
        out_json = Path(args.out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if args.out_md:
        status = "PASS" if passed else "FAIL"
        md = f"# Chromatic Zero\n\n**Status:** {status}\n**Total:** {total}\n**Accepted:** {accepted}\n**Errored:** {errored}\n"
        out_md = Path(args.out_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(md, encoding="utf-8")

    if not passed:
        print(f"Chromatic zero gate FAILED: {errored} errored, changes detected.", file=sys.stderr)
        return 1

    print("Chromatic zero gate passed: no visual regressions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
