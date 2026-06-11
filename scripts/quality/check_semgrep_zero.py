#!/usr/bin/env python3
"""Gate script: assert zero Semgrep findings in a SARIF file (per §9.1)."""
from __future__ import absolute_import

import sys
from pathlib import Path

# Make ``scripts.*`` importable when this file is invoked as a path from a
# foreign cwd (the scanner-matrix lane runs with ``cwd=repo_dir`` so the
# QZP repo root is not on ``sys.path`` by default).
if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality._sarif_zero_gate import run_zero_gate  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_zero_gate(provider="Semgrep"))
