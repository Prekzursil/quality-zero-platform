"""Tests for validate_workflow_paths.py (per §B.2.2)."""
from __future__ import absolute_import

import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.validate_workflow_paths import validate_paths, main


class ValidatePathsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "storybook-static").mkdir()
        (self.root / "applitools.config.js").write_text("{}", "utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_valid_storybook_dir(self):
        errors = validate_paths(self.root, ["storybook-static"])
        self.assertEqual(errors, [])

    def test_valid_config_file(self):
        errors = validate_paths(self.root, ["applitools.config.js"])
        self.assertEqual(errors, [])

    def test_traversal_rejected(self):
        errors = validate_paths(self.root, ["../../etc/passwd"])
        self.assertEqual(len(errors), 1)
        self.assertIn("Path validation failed", errors[0])

    def test_empty_path_rejected(self):
        errors = validate_paths(self.root, [""])
        self.assertEqual(len(errors), 1)

    def test_multiple_paths_mixed(self):
        errors = validate_paths(self.root, ["storybook-static", "../../escape"])
        self.assertEqual(len(errors), 1)

    def test_multiple_valid_paths(self):
        errors = validate_paths(self.root, ["storybook-static", "applitools.config.js"])
        self.assertEqual(errors, [])


class MainTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "valid-dir").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_main_returns_zero_on_valid(self):
        rc = main(["--repo-root", str(self.root), "--path", "valid-dir"])
        self.assertEqual(rc, 0)

    def test_main_returns_one_on_traversal(self):
        rc = main(["--repo-root", str(self.root), "--path", "../../escape"])
        self.assertEqual(rc, 1)

    def test_main_multiple_paths(self):
        rc = main(["--repo-root", str(self.root), "--path", "valid-dir", "--path", "../../escape"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
