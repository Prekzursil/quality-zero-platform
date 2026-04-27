"""Shared fixtures for ``test_run_coverage_gate*`` test files.

Both ``test_run_coverage_gate.py`` (covers shell-invocation, mode resolution,
and main path) and ``test_run_coverage_gate_extra.py`` (covers non-regression
mode, baselining, and edge entry-point paths) reuse the same shell-mock
runner and the same temp-dir/coverage-XML fixture. Centralising them avoids
the ~60-line duplication block qlty's smells gate previously flagged while
keeping each test file under its own coverage scope.
"""
from __future__ import absolute_import

import tempfile
from pathlib import Path
from typing import List
from unittest.mock import patch

from scripts.quality import run_coverage_gate


def assert_run_shell_invocation(
    test_case,
    *,
    shell_name: str,
    resolved_path: str,
    expected_argv: List[str],
) -> None:
    """Assert that ``run_coverage_gate._run_shell`` resolves to ``expected_argv``."""
    with tempfile.TemporaryDirectory() as temp_dir:
        cwd = Path(temp_dir)
        with (
            patch(
                "scripts.quality.run_coverage_gate._path_exists",
                side_effect=lambda raw_path: raw_path == resolved_path,
            ),
            patch(
                "scripts.quality.run_coverage_gate.subprocess.run",
                return_value=object(),
            ) as mock_run,
        ):
            run_coverage_gate._run_shell(
                "echo coverage", shell_name=shell_name, cwd=cwd
            )

    mock_run.assert_called_once_with(
        expected_argv,
        cwd=cwd,
        input="echo coverage",
        text=True,
        shell=False,
        check=True,
    )


def make_coverage_assert_fixture():
    """Return ``(temp_dir, repo_dir, platform_dir, coverage_dir, coverage_cfg)``."""
    temp_dir = tempfile.TemporaryDirectory()
    repo_dir = Path(temp_dir.name) / "repo"
    repo_dir.mkdir()
    platform_dir = Path(temp_dir.name) / "platform"
    platform_dir.mkdir()
    coverage_dir = repo_dir / "coverage"
    coverage_dir.mkdir()
    (coverage_dir / "platform-coverage.xml").write_text(
        (
            "<coverage lines-valid=\"1\" lines-covered=\"1\"><packages>"
            "<package><classes><class filename=\"src/app.py\"><lines>"
            '<line number="1" hits="1" />'
            "</lines></class></classes></package></packages></coverage>"
        ),
        encoding="utf-8",
    )
    coverage = {
        "inputs": [
            {
                "format": "xml",
                "name": "platform",
                "path": str(coverage_dir / "platform-coverage.xml"),
            },
        ],
        "require_sources": ["src/app.py"],
        "min_percent": 100.0,
        "branch_min_percent": 85.0,
    }
    return temp_dir, repo_dir, platform_dir, coverage_dir, coverage
