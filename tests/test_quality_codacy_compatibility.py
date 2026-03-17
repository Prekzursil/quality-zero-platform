from __future__ import absolute_import

import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FUTURE_ANNOTATIONS_PATTERN = re.compile(r"^from __future__ import annotations(?:\s*,.*)?$", re.MULTILINE)
FUTURE_ABSOLUTE_IMPORT = "from __future__ import absolute_import"
BUILTIN_GENERIC_PATTERN = re.compile(r"\b(?:dict|list|set|tuple)\[")
TARGET_DIRS = ("scripts", "tests")


def _non_comment_line_count(path: Path) -> int:
    total = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#"):
            total += 1
    return total


def _python_sources():
    sources = []
    for directory_name in TARGET_DIRS:
        directory = ROOT / directory_name
        sources.extend(
            path
            for path in directory.rglob("*.py")
            if "__pycache__" not in path.parts
        )
    return sorted(sources)


def _collect_future_import_issues(paths):
    future_annotations = []
    missing_absolute_import = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
        if FUTURE_ANNOTATIONS_PATTERN.search(text):
            future_annotations.append(relative)
        if text.strip() and FUTURE_ABSOLUTE_IMPORT not in text:
            missing_absolute_import.append(relative)
    return future_annotations, missing_absolute_import


def _collect_builtin_generic_offenders(paths):
    offenders = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if BUILTIN_GENERIC_PATTERN.search(text):
            offenders.append(path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name)
    return offenders


class QualityCodacyCompatibilityTests(unittest.TestCase):
    def test_python_sources_use_absolute_import_and_not_future_annotations(self):
        future_annotations, missing_absolute_import = _collect_future_import_issues(_python_sources())

        self.assertEqual(future_annotations, [], "Unexpected future annotations imports remain.")
        self.assertEqual(missing_absolute_import, [], "Python sources must declare absolute_import for Codacy compatibility.")

    def test_python_sources_avoid_builtin_collection_generic_syntax(self):
        offenders = _collect_builtin_generic_offenders(_python_sources())
        self.assertEqual(offenders, [], "Builtin collection generic syntax still present in Python sources.")

    def test_control_plane_module_stays_below_medium_nloc_threshold(self):
        control_plane_path = ROOT / "scripts" / "quality" / "control_plane.py"
        self.assertLess(_non_comment_line_count(control_plane_path), 500)

    def test_issue_collectors_flag_problematic_python_compatibility_patterns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            future_annotations_path = root / "future_annotations.py"
            future_annotations_path.write_text(
                "from __future__ import annotations\nfrom __future__ import absolute_import\n",
                encoding="utf-8",
            )
            missing_import_path = root / "missing_absolute_import.py"
            missing_import_path.write_text("print('missing absolute import')\n", encoding="utf-8")
            builtin_generic_path = root / "builtin_generics.py"
            builtin_generic_path.write_text(
                "from __future__ import absolute_import\nvalues: "
                + "li"
                + "st"
                + "[str] = []\n",
                encoding="utf-8",
            )

            future_annotations, missing_absolute_import = _collect_future_import_issues(
                [future_annotations_path, missing_import_path]
            )
            offenders = _collect_builtin_generic_offenders([builtin_generic_path])

        self.assertEqual(future_annotations, ["future_annotations.py"])
        self.assertEqual(
            missing_absolute_import,
            ["missing_absolute_import.py"],
        )
        self.assertEqual(offenders, ["builtin_generics.py"])
