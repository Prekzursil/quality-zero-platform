#!/usr/bin/env python3
"""Gate script: assert zero Semgrep findings in a SARIF file (per §9.1).

Usage:
    python scripts/quality/check_semgrep_zero.py --sarif results.sarif.json [--out-json out.json] [--out-md out.md]

Exits 0 if zero findings, 1 otherwise.
"""
from __future__ import absolute_import

import argparse
import json
import sys
from pathlib import Path
from typing import List


def _count_sarif_results(data: dict) -> int:
    """Count total results across all SARIF runs."""
    total = 0
    for run in data.get("runs", []):
        if isinstance(run, dict):
            results = run.get("results", [])
            if isinstance(results, list):
                total += len(results)
    return total


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Semgrep zero-finding gate")
    parser.add_argument("--sarif", required=True, help="Path to Semgrep SARIF output")
    parser.add_argument("--out-json", default=None, help="Write JSON summary")
    parser.add_argument("--out-md", default=None, help="Write Markdown summary")
    args = parser.parse_args(argv)

    sarif_path = Path(args.sarif)
    if not sarif_path.exists():
        print(f"SARIF file not found: {sarif_path}", file=sys.stderr)
        return 1

    data = json.loads(sarif_path.read_text(encoding="utf-8"))
    count = _count_sarif_results(data)

    summary = {"provider": "Semgrep", "total_findings": count, "pass": count == 0}

    if args.out_json:
        out_json = Path(args.out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if args.out_md:
        status = "PASS" if count == 0 else "FAIL"
        md = f"# Semgrep Zero\n\n**Status:** {status}\n**Findings:** {count}\n"
        out_md = Path(args.out_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(md, encoding="utf-8")

    if count > 0:
        print(f"Semgrep zero gate FAILED: {count} finding(s) detected.", file=sys.stderr)
        return 1

    print("Semgrep zero gate passed: 0 findings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
