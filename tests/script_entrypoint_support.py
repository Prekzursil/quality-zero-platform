"""Script entrypoint support."""

from __future__ import absolute_import

import os
import runpy
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


def run_script_entrypoint_failure(script_relative_path: str) -> int:
    """Run a script as ``__main__`` and return its exit code."""
    script_path = Path(script_relative_path).resolve()
    root_text = str(Path.cwd().resolve())
    trimmed_sys_path = [item for item in sys.path if item != root_text]
    with tempfile.TemporaryDirectory() as tmp, patch.dict(
        "os.environ",
        {},
        clear=True,
    ), patch.object(
        sys,
        "argv",
        [str(script_path)],
    ), patch.object(
        sys, "path", trimmed_sys_path[:]
    ):
        cwd = Path(tmp)
        previous = Path.cwd()
        os.chdir(cwd)
        try:
            try:
                runpy.run_path(str(script_path), run_name="__main__")
            except SystemExit as exc:
                return int(exc.code)
        finally:
            os.chdir(previous)
    raise AssertionError(f"{script_relative_path} did not exit")
