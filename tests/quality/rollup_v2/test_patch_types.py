"""Tests for PatchResult and PatchDeclined types (per design §A.1.3 + §B.3.11)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult


class PatchResultTests(unittest.TestCase):
    """Tests for PatchResult frozen dataclass."""

    def test_valid_construction(self):
        pr = PatchResult(
            unified_diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new",
            confidence="high",
            category="broad-except",
            generator_version="1.0.0",
            touches_files=frozenset({Path("x.py")}),
        )
        self.assertEqual(pr.confidence, "high")
        self.assertEqual(pr.category, "broad-except")
        self.assertIsInstance(pr.touches_files, frozenset)

    def test_frozen(self):
        pr = PatchResult(
            unified_diff="diff",
            confidence="high",
            category="c",
            generator_version="1",
            touches_files=frozenset({Path("a.py")}),
        )
        with self.assertRaises(Exception):
            pr.confidence = "low"  # type: ignore[misc]

    def test_invalid_confidence_raises(self):
        with self.assertRaises(AssertionError):
            PatchResult(
                unified_diff="diff",
                confidence="invalid",  # type: ignore[arg-type]
                category="c",
                generator_version="1",
                touches_files=frozenset({Path("a.py")}),
            )

    def test_empty_touches_files_raises(self):
        with self.assertRaises(AssertionError):
            PatchResult(
                unified_diff="diff",
                confidence="high",
                category="c",
                generator_version="1",
                touches_files=frozenset(),
            )

    def test_touches_files_must_contain_paths(self):
        with self.assertRaises(AssertionError):
            PatchResult(
                unified_diff="diff",
                confidence="high",
                category="c",
                generator_version="1",
                touches_files=frozenset({"a.py"}),  # type: ignore[arg-type]
            )

    def test_all_confidence_values(self):
        for conf in ("high", "medium", "low"):
            pr = PatchResult(
                unified_diff="diff",
                confidence=conf,  # type: ignore[arg-type]
                category="c",
                generator_version="1",
                touches_files=frozenset({Path("a.py")}),
            )
            self.assertEqual(pr.confidence, conf)

    def test_multiple_touches_files(self):
        pr = PatchResult(
            unified_diff="diff",
            confidence="medium",
            category="c",
            generator_version="1",
            touches_files=frozenset({Path("a.py"), Path("b.py")}),
        )
        self.assertEqual(len(pr.touches_files), 2)


class PatchDeclinedTests(unittest.TestCase):
    """Tests for PatchDeclined frozen dataclass."""

    def test_valid_construction(self):
        pd = PatchDeclined(
            reason_code="requires-ast-rewrite",
            reason_text="Need AST-level transformation",
            suggested_tier="llm-fallback",
        )
        self.assertEqual(pd.reason_code, "requires-ast-rewrite")
        self.assertEqual(pd.suggested_tier, "llm-fallback")

    def test_frozen(self):
        pd = PatchDeclined(
            reason_code="cross-file-change",
            reason_text="text",
            suggested_tier="human-only",
        )
        with self.assertRaises(Exception):
            pd.reason_code = "other"  # type: ignore[misc]

    def test_invalid_reason_code_raises(self):
        with self.assertRaises(AssertionError):
            PatchDeclined(
                reason_code="invalid-reason",  # type: ignore[arg-type]
                reason_text="text",
                suggested_tier="skip",
            )

    def test_invalid_suggested_tier_raises(self):
        with self.assertRaises(AssertionError):
            PatchDeclined(
                reason_code="ambiguous-fix",
                reason_text="text",
                suggested_tier="invalid-tier",  # type: ignore[arg-type]
            )

    def test_all_valid_reason_codes(self):
        for code in (
            "requires-ast-rewrite",
            "cross-file-change",
            "ambiguous-fix",
            "provider-data-insufficient",
            "path-traversal-rejected",
        ):
            pd = PatchDeclined(
                reason_code=code,  # type: ignore[arg-type]
                reason_text="text",
                suggested_tier="skip",
            )
            self.assertEqual(pd.reason_code, code)

    def test_all_valid_suggested_tiers(self):
        for tier in ("llm-fallback", "human-only", "skip"):
            pd = PatchDeclined(
                reason_code="ambiguous-fix",
                reason_text="text",
                suggested_tier=tier,  # type: ignore[arg-type]
            )
            self.assertEqual(pd.suggested_tier, tier)


if __name__ == "__main__":
    unittest.main()
