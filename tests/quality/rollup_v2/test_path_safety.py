"""Tests for rollup_v2 path safety wrapper (per design §B.2)."""
from __future__ import absolute_import

import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.path_safety import (
    PathEscapedRootError,
    validate_finding_file,
)


class ValidateFindingFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_valid_relative_path_passes(self):
        result = validate_finding_file("src/a.py", self.root)
        self.assertEqual(result, (self.root / "src" / "a.py").resolve())

    def test_absolute_escape_raises(self):
        with self.assertRaises(PathEscapedRootError):
            validate_finding_file("/etc/passwd", self.root)

    def test_dotdot_escape_raises(self):
        with self.assertRaises(PathEscapedRootError):
            validate_finding_file("../../etc/passwd", self.root)

    def test_empty_path_raises(self):
        with self.assertRaises(PathEscapedRootError):
            validate_finding_file("", self.root)


if __name__ == "__main__":
    unittest.main()
