"""Ensure patch_harness.py is loaded during standard test discovery."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.quality.rollup_v2.patch_harness import PatchGeneratorGoldenTests  # noqa: F401


class HarnessLoaderTest(unittest.TestCase):
    def test_harness_class_exists(self):
        # The class is imported at module load; the assertion proves the
        # symbol is exported (catches accidental rename) rather than the
        # tautological "is not None" check Sonar python:S5914 flagged.
        self.assertTrue(issubclass(PatchGeneratorGoldenTests, unittest.TestCase))

    def test_harness_has_dynamically_attached_methods(self):
        """At least the broad_except smoke fixture should be attached."""
        method_names = [m for m in dir(PatchGeneratorGoldenTests) if m.startswith("test_")]
        self.assertIn("test_broad_except_smoke", method_names)


if __name__ == "__main__":
    unittest.main()
