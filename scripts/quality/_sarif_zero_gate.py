#!/usr/bin/env python3
"""Shared zero-finding gate for any SARIF-emitting analyzer.

Used by ``check_codeql_zero.py`` and ``check_semgrep_zero.py``; both gates count
results across all SARIF runs and pass only when the total is zero. Centralising
the dispatch keeps the per-analyzer wrappers ~3 lines and removes the 69-line
duplication block qlty's smells gate previously flagged.
"""
from __future__ import absolute_import

import argparse
import json
import sys
from pathlib import Path
from typing import List


def count_sarif_results(data: dict) -> int:
    """Count total results across all SARIF runs."""
    total = 0
    for run in data.get("runs", []):
        if isinstance(run, dict):
            results = run.get("results", [])
            if isinstance(results, list):
                total += len(results)
    return total


def _write_summary_files(
    *,
    summary: dict,
    out_json: str | None,
    out_md: str | None,
    provider: str,
    count: int,
) -> None:
    if out_json:
        path = Path(out_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if out_md:
        status = "PASS" if count == 0 else "FAIL"
        md = f"# {provider} Zero\n\n**Status:** {status}\n**Findings:** {count}\n"
        path = Path(out_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md, encoding="utf-8")


def run_zero_gate(*, provider: str, argv: List[str] | None = None) -> int:
    """Parse CLI args, count SARIF findings, write summaries, return exit code."""
    parser = argparse.ArgumentParser(
        description=f"{provider} zero-finding gate",
    )
    parser.add_argument(
        "--sarif", required=True, help=f"Path to {provider} SARIF output",
    )
    parser.add_argument("--out-json", default=None, help="Write JSON summary")
    parser.add_argument("--out-md", default=None, help="Write Markdown summary")
    args = parser.parse_args(argv)

    sarif_path = Path(args.sarif)
    if not sarif_path.exists():
        print(f"SARIF file not found: {sarif_path}", file=sys.stderr)
        return 1

    data = json.loads(sarif_path.read_text(encoding="utf-8"))
    count = count_sarif_results(data)
    summary = {"provider": provider, "total_findings": count, "pass": count == 0}
    _write_summary_files(
        summary=summary,
        out_json=args.out_json,
        out_md=args.out_md,
        provider=provider,
        count=count,
    )

    if count > 0:
        print(
            f"{provider} zero gate FAILED: {count} finding(s) detected.",
            file=sys.stderr,
        )
        return 1
    print(f"{provider} zero gate passed: 0 findings.")
    return 0
