"""Tests for Finding dataclass (per design §3.1 + §A.3.2 + §A.4.1)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.schema.corroborator import Corroborator
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    CATEGORY_GROUP_SECURITY,
    CATEGORY_GROUP_STYLE,
    SCHEMA_VERSION,
    Finding,
)


def _make_corroborator():
    return Corroborator.from_provider("Codacy", "Pylint_W0703", None, "broad except")


class FindingTests(unittest.TestCase):
    def test_schema_version_is_qzp_finding_1(self):
        self.assertEqual(SCHEMA_VERSION, "qzp-finding/1")

    def test_category_group_constants(self):
        self.assertEqual(CATEGORY_GROUP_SECURITY, "security")
        self.assertEqual(CATEGORY_GROUP_QUALITY, "quality")
        self.assertEqual(CATEGORY_GROUP_STYLE, "style")

    def test_all_required_fields(self):
        f = Finding(
            schema_version=SCHEMA_VERSION,
            finding_id="qzp-0001",
            file="scripts/quality/coverage_parsers.py",
            line=42,
            end_line=42,
            column=5,
            category="broad-except",
            category_group=CATEGORY_GROUP_QUALITY,
            severity="medium",
            corroboration="single",
            primary_message="Catch a more specific exception",
            corroborators=(_make_corroborator(),),
            fix_hint="Narrow the exception type",
            patch=None,
            patch_source="none",
            patch_confidence=None,
            context_snippet="    try:\n        parse_coverage(path)\n    except Exception as e:\n        log.warning(...)",
            source_file_hash="sha256:deadbeef",
            cwe=None,
            autofixable=False,
            tags=(),
        )
        self.assertEqual(f.schema_version, SCHEMA_VERSION)
        self.assertEqual(f.category_group, "quality")
        self.assertEqual(len(f.corroborators), 1)

    def test_frozen(self):
        f = Finding(
            schema_version=SCHEMA_VERSION,
            finding_id="qzp-0001",
            file="a.py",
            line=1,
            end_line=1,
            column=None,
            category="broad-except",
            category_group=CATEGORY_GROUP_QUALITY,
            severity="medium",
            corroboration="single",
            primary_message="m",
            corroborators=(),
            fix_hint=None,
            patch=None,
            patch_source="none",
            patch_confidence=None,
            context_snippet="",
            source_file_hash="sha256:x",
            cwe=None,
            autofixable=False,
            tags=(),
        )
        with self.assertRaises(Exception):
            f.line = 2  # type: ignore[misc]

    def test_invalid_category_group_raises(self):
        with self.assertRaises(AssertionError):
            Finding(
                schema_version=SCHEMA_VERSION,
                finding_id="x",
                file="a.py",
                line=1,
                end_line=1,
                column=None,
                category="c",
                category_group="invalid",   # <- not security/quality/style
                severity="low",
                corroboration="single",
                primary_message="m",
                corroborators=(),
                fix_hint=None,
                patch=None,
                patch_source="none",
                patch_confidence=None,
                context_snippet="",
                source_file_hash="",
                cwe=None,
                autofixable=False,
                tags=(),
            )


    def test_patch_error_defaults_to_none_and_is_optional(self):
        f = Finding(
            schema_version=SCHEMA_VERSION,
            finding_id="x",
            file="a.py",
            line=1,
            end_line=1,
            column=None,
            category="c",
            category_group=CATEGORY_GROUP_QUALITY,
            severity="low",
            corroboration="single",
            primary_message="m",
            corroborators=(),
            fix_hint=None,
            patch=None,
            patch_source="none",
            patch_confidence=None,
            context_snippet="",
            source_file_hash="",
            cwe=None,
            autofixable=False,
            tags=(),
            patch_error=None,
        )
        self.assertIsNone(f.patch_error)

    def test_patch_error_accepts_non_empty_string(self):
        f = Finding(
            schema_version=SCHEMA_VERSION,
            finding_id="x",
            file="a.py",
            line=1,
            end_line=1,
            column=None,
            category="c",
            category_group=CATEGORY_GROUP_QUALITY,
            severity="low",
            corroboration="single",
            primary_message="m",
            corroborators=(),
            fix_hint=None,
            patch=None,
            patch_source="none",
            patch_confidence=None,
            context_snippet="",
            source_file_hash="",
            cwe=None,
            autofixable=False,
            tags=(),
            patch_error="ValueError: unable to parse snippet",
        )
        self.assertEqual(f.patch_error, "ValueError: unable to parse snippet")


if __name__ == "__main__":
    unittest.main()
