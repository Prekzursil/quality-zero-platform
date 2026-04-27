#!/usr/bin/env python3
"""Gate script: assert zero Semgrep findings in a SARIF file (per §9.1).

Usage:
    python scripts/quality/check_semgrep_zero.py --sarif results.sarif.json [--out-json out.json] [--out-md out.md]

Exits 0 if zero findings, 1 otherwise. The actual gate logic lives in
``scripts/quality/_sarif_zero_helpers.py`` — shared with check_codeql_zero.
"""

from __future__ import absolute_import

import sys
from typing import List

from scripts.quality._sarif_zero_helpers import run_sarif_zero_gate


def main(argv: List[str] | None = None) -> int:
    """Run the Semgrep SARIF zero-finding gate."""
    return run_sarif_zero_gate(
        provider_name="Semgrep",
        section_anchor="§9.1",
        argv=argv,
    )


if __name__ == "__main__":
    sys.exit(main())
