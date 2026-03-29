"""Test script entrypoint support."""

from __future__ import absolute_import

import unittest
from pathlib import Path

from tests.script_entrypoint_support import run_script_entrypoint_failure


class ScriptEntrypointSupportTests(unittest.TestCase):
    """Script Entrypoint Support Tests."""

    def test_run_script_entrypoint_failure_raises_when_script_never_exits(self) -> None:
        """Cover run script entrypoint failure raises when script never exits."""
        script_path = Path("tests/fixtures/no_exit_script.py").resolve()
        with self.assertRaisesRegex(AssertionError, "did not exit"):
            run_script_entrypoint_failure(str(script_path))
