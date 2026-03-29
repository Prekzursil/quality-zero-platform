"""Test script entrypoint support."""

from __future__ import absolute_import

import unittest
from pathlib import Path

from tests.script_entrypoint_support import run_script_entrypoint_failure


class ScriptEntrypointSupportTests(unittest.TestCase):
    """Script Entrypoint Support Tests."""

    def test_run_script_entrypoint_failure_handles_none_exit_code(self) -> None:
        """Treat ``SystemExit(None)`` as a zero exit code."""
        script_path = Path("tests/fixtures/none_exit_script.py").resolve()
        self.assertEqual(run_script_entrypoint_failure(str(script_path)), 0)

    def test_run_script_entrypoint_failure_handles_string_exit_code(self) -> None:
        """Convert string exit codes into integers for callers."""
        script_path = Path("tests/fixtures/string_exit_script.py").resolve()
        self.assertEqual(run_script_entrypoint_failure(str(script_path)), 7)

    def test_run_script_entrypoint_failure_raises_when_script_never_exits(self) -> None:
        """Cover run script entrypoint failure raises when script never exits."""
        script_path = Path("tests/fixtures/no_exit_script.py").resolve()
        with self.assertRaisesRegex(AssertionError, "did not exit"):
            run_script_entrypoint_failure(str(script_path))
