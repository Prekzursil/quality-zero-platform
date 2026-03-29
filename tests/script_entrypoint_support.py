"""Script entrypoint support."""

from __future__ import absolute_import

import runpy
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict
from unittest.mock import patch


def run_script_entrypoint_failure(script_relative_path: str) -> int:
    """Run a script as ``__main__`` and return its exit code."""
    script_path = Path(script_relative_path).resolve()
    bootstrap = "\n".join(
        [
            "import runpy, sys",
            "script_path = sys.argv[1]",
            "sys.argv = [script_path]",
            "try:",
            "    runpy.run_path(script_path, run_name='__main__')",
            "except SystemExit as exc:",
            "    code = exc.code",
            "    if code is None:",
            "        raise SystemExit(0)",
            "    if isinstance(code, int):",
            "        raise SystemExit(code)",
            "    raise SystemExit(1)",
            "raise SystemExit(255)",
        ]
    )
    with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True):
        completed = subprocess.run(
            [sys.executable, "-c", bootstrap, str(script_path)],
            cwd=Path(tmp),
            env={},
            check=False,
            capture_output=True,
            text=True,
        )
    if completed.returncode == 255:
        raise AssertionError(f"{script_path} did not exit")
    return int(completed.returncode)


def assert_in_process_entrypoint_failure(test_case, script_relative_path: str) -> None:
    """Run one script in-process as ``__main__`` and assert a failing exit code."""
    script_path = Path(script_relative_path).resolve()
    repo_root = str(script_path.parents[2])
    original_path = list(sys.path)
    try:
        sys.path[:] = [entry for entry in sys.path if entry != repo_root]
        with (
            patch.object(sys, "argv", [script_path.name]),
            patch.dict("os.environ", {}, clear=True),
            test_case.assertRaises(SystemExit) as exit_info,
        ):
            runpy.run_path(str(script_path), run_name="__main__")
    finally:
        inserted_path = repo_root in sys.path
        sys.path[:] = original_path
    test_case.assertTrue(inserted_path)
    test_case.assertEqual(exit_info.exception.code, 1)


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
