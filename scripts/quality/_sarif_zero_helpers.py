"""Shared SARIF zero-finding gate runner (per §9.x).

Both CodeQL and Semgrep zero gates parse SARIF v2 output and assert that
the total ``results`` count across all runs is zero. Their CLI shapes
were 90% identical (68-line clone per qlty's smell report). This
module hosts the one canonical implementation; the per-tool wrappers
(``check_codeql_zero.py``, ``check_semgrep_zero.py``) reduce to a
thin call.
"""

from __future__ import absolute_import

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping


def count_sarif_results(data: Mapping[str, object]) -> int:
    """Count total ``results[]`` entries across every SARIF ``runs[]`` entry."""
    total = 0
    for run in data.get("runs", []):
        if isinstance(run, dict):
            results = run.get("results", [])
            if isinstance(results, list):
                total += len(results)
    return total


def _build_parser(
    *, provider_name: str, section_anchor: str
) -> argparse.ArgumentParser:
    """Build the argparse spec for one provider's zero gate."""
    parser = argparse.ArgumentParser(
        description=f"{provider_name} zero-finding gate ({section_anchor})",
    )
    parser.add_argument(
        "--sarif", required=True, help=f"Path to {provider_name} SARIF output"
    )
    parser.add_argument("--out-json", default=None, help="Write JSON summary")
    parser.add_argument("--out-md", default=None, help="Write Markdown summary")
    return parser


def _write_summary_outputs(
    *,
    args: argparse.Namespace,
    summary: Dict[str, Any],
    provider_name: str,
    count: int,
) -> None:
    """Persist optional JSON / Markdown summaries when the args request them."""
    if args.out_json:
        out_json = Path(args.out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if args.out_md:
        status = "PASS" if count == 0 else "FAIL"
        md = f"# {provider_name} Zero\n\n**Status:** {status}\n**Findings:** {count}\n"
        out_md = Path(args.out_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(md, encoding="utf-8")


def run_sarif_zero_gate(
    *,
    provider_name: str,
    section_anchor: str,
    argv: List[str] | None = None,
) -> int:
    """Run the shared SARIF zero-finding gate for one provider.

    Returns exit code 0 on zero findings, 1 on any non-zero count or
    missing SARIF file. ``provider_name`` is the display label
    (``"CodeQL"`` / ``"Semgrep"``); ``section_anchor`` is the QZP
    design-doc reference shown in the argparse description.
    """
    parser = _build_parser(
        provider_name=provider_name, section_anchor=section_anchor
    )
    args = parser.parse_args(argv)

    sarif_path = Path(args.sarif)
    if not sarif_path.exists():
        print(f"SARIF file not found: {sarif_path}", file=sys.stderr)
        return 1

    data = json.loads(sarif_path.read_text(encoding="utf-8"))
    count = count_sarif_results(data)
    summary = {
        "provider": provider_name,
        "total_findings": count,
        "pass": count == 0,
    }
    _write_summary_outputs(
        args=args, summary=summary, provider_name=provider_name, count=count
    )

    if count > 0:
        print(
            f"{provider_name} zero gate FAILED: {count} finding(s) detected.",
            file=sys.stderr,
        )
        return 1

    print(f"{provider_name} zero gate passed: 0 findings.")
    return 0
