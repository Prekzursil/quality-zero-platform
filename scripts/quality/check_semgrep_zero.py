#!/usr/bin/env python3
"""Gate script: assert zero Semgrep findings in a SARIF file (per §9.1)."""
from __future__ import absolute_import

import sys

from scripts.quality._sarif_zero_gate import run_zero_gate

if __name__ == "__main__":
    sys.exit(run_zero_gate(provider="Semgrep"))
