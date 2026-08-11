"""Workspace isolation helpers for tests that drive CLI entrypoints.

Every strict-zero lane writer resolves its ``--out-json``/``--out-md`` defaults
against ``Path.cwd()`` (see ``scripts/quality/common.py`` ``write_report`` ->
``safe_output_path``). A test that invokes one of those entrypoints without
first moving the current working directory therefore writes REAL lane reports
into the repository root, leaving a contributor with a dirty tree after running
``bash scripts/verify``.

``isolated_cwd`` is the one-line fix: run the entrypoint inside a throwaway
directory so its default output paths land somewhere disposable.
"""

from __future__ import absolute_import

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def isolated_cwd() -> Iterator[Path]:
    """Run the wrapped block with the process cwd inside a throwaway directory.

    Yields the resolved temporary directory so a caller can assert on paths the
    entrypoint derived from ``Path.cwd()``. The previous working directory is
    restored even when the block raises.
    """
    previous = Path.cwd()
    with tempfile.TemporaryDirectory() as temp_dir:
        resolved = Path(temp_dir).resolve()
        os.chdir(resolved)
        try:
            yield resolved
        finally:
            os.chdir(previous)
