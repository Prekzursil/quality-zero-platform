"""Script entrypoint support."""

from __future__ import absolute_import

import os
import runpy
import sys
import tempfile
from pathlib import Path
from typing import Dict
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
                if exc.code is None:
                    return 0
                if isinstance(exc.code, int):
                    return exc.code
                return int(str(exc.code))
        finally:
            os.chdir(previous)
    raise AssertionError(f"{script_relative_path} did not exit")


def assert_main_reports_provider_failure(
    test_case,
    module,
    config: Dict[str, object],
) -> None:
    """Assert that one provider-backed main() path reports a request failure."""
    with patch.dict("os.environ", config["env"], clear=False), patch.object(
        module,
        "_parse_args",
        return_value=config["args"],
    ), patch.object(
        module,
        str(config["operation_name"]),
        side_effect=RuntimeError(str(config["failure_message"])),
    ), patch.object(module, "write_report", return_value=0) as write_report_mock:
        test_case.assertEqual(module.main(), 1)
    test_case.assertEqual(
        write_report_mock.call_args.args[0]["findings"],
        [str(config["expected_finding"])],
    )
