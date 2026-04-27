"""Shared fixtures for ``test_quality_common*`` test files.

Both ``test_quality_common.py`` (covers ``ReportSpec`` + I/O surfaces) and
``test_quality_common_extra.py`` (covers normalization helpers) use the same
``_temporary_cwd`` context manager and the same explicit/inferred coverage
fixtures. Extracting them here removes the ~47-line duplication block qlty's
smells gate previously flagged.
"""
from __future__ import absolute_import

import contextlib
import os
from pathlib import Path
from typing import Iterator

from scripts.quality.common import normalize_coverage


@contextlib.contextmanager
def temporary_cwd(target: Path) -> Iterator[None]:
    """Push ``target`` as the working directory; restore previous on exit."""
    previous = Path.cwd()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(previous)


def normalized_explicit_coverage() -> dict:
    """Return ``normalize_coverage(...)`` for the explicit-config scenario."""
    return normalize_coverage(
        {
            "runner": " ",
            "shell": "",
            "command": "  qlty check  ",
            "inputs": [
                {"format": "xml", "name": "coverage", "path": "coverage.xml"}
            ],
            "require_sources": [" source-a ", "source-a", "source-b"],
            "min_percent": "98.5",
            "assert_mode": {"default": "", "python": " warn "},
            "evidence_note": "  note  ",
            "setup": {
                "python": " 3.11 ",
                "node": " 20 ",
                "go": " 1.22 ",
                "dotnet": " 8 ",
                "rust": "yes",
                "system_packages": [" git ", "curl", "git"],
                "java": {"distribution": " temurin ", "version": " 21 "},
            },
        }
    )


def inferred_coverage() -> dict:
    """Return ``normalize_coverage(...)`` for the inferred-from-command scenario."""
    return normalize_coverage(
        {
            "command": (
                "python -m pytest --cov=scripts --cov=scripts.quality.assert_coverage_100 "
                "--cov=scripts.quality.check_sentry_zero && "
                "gcovr --filter '.*/src/.*' && "
                "npm --prefix airline-gui test -- --coverage --watch=false"
            ),
            "inputs": [
                {"format": "xml", "name": "coverage", "path": "coverage.xml"},
                {
                    "format": "lcov",
                    "name": "frontend",
                    "path": "airline-gui/coverage/lcov.info",
                },
            ],
        }
    )
