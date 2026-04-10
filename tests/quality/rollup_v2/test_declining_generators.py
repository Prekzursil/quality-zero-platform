"""Tests for declining patch generators (per §5.1 — categories that always return PatchDeclined)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.patches import (
    command_injection,
    coverage_gap,
    cyclic_import,
    duplicate_code,
    insecure_random,
    naming_convention,
    open_redirect,
    shadowed_builtin,
    todo_comment,
    too_complex,
    too_long,
    weak_crypto,
)
from scripts.quality.rollup_v2.types.corroborator import Corroborator
from scripts.quality.rollup_v2.types.finding import SCHEMA_VERSION, Finding
from scripts.quality.rollup_v2.types.patch import PatchDeclined


def _make_finding(category: str, category_group: str = "quality") -> Finding:
    """Build a minimal Finding for decline testing."""
    corr = Corroborator.from_provider(
        provider="Codacy",
        rule_id="FIXTURE",
        rule_url=None,
        original_message="test message",
    )
    return Finding(
        schema_version=SCHEMA_VERSION,
        finding_id="qzp-0000",
        file="test.py",
        line=1,
        end_line=1,
        column=None,
        category=category,
        category_group=category_group,
        severity="medium",
        corroboration="single",
        primary_message="test message",
        corroborators=(corr,),
        fix_hint=None,
        patch=None,
        patch_source="none",
        patch_confidence=None,
        context_snippet="",
        source_file_hash="sha256:fixture",
        cwe=None,
        autofixable=False,
        tags=(),
    )


_SOURCE = "x = 1\n"
_ROOT = Path("/tmp/repo")


class DecliningGeneratorTests(unittest.TestCase):
    """Each declining generator must return PatchDeclined with the correct tier."""

    def test_todo_comment_declines_human_only(self):
        finding = _make_finding("todo-comment")
        result = todo_comment.generate(finding, _SOURCE, _ROOT)
        self.assertIsInstance(result, PatchDeclined)
        self.assertEqual(result.suggested_tier, "human-only")

    def test_insecure_random_declines_llm_fallback(self):
        finding = _make_finding("insecure-random", "security")
        result = insecure_random.generate(finding, _SOURCE, _ROOT)
        self.assertIsInstance(result, PatchDeclined)
        self.assertEqual(result.suggested_tier, "llm-fallback")

    def test_weak_crypto_declines_llm_fallback(self):
        finding = _make_finding("weak-crypto", "security")
        result = weak_crypto.generate(finding, _SOURCE, _ROOT)
        self.assertIsInstance(result, PatchDeclined)
        self.assertEqual(result.suggested_tier, "llm-fallback")

    def test_naming_convention_declines_llm_fallback(self):
        finding = _make_finding("naming-convention")
        result = naming_convention.generate(finding, _SOURCE, _ROOT)
        self.assertIsInstance(result, PatchDeclined)
        self.assertEqual(result.suggested_tier, "llm-fallback")

    def test_open_redirect_declines_llm_fallback(self):
        finding = _make_finding("open-redirect", "security")
        result = open_redirect.generate(finding, _SOURCE, _ROOT)
        self.assertIsInstance(result, PatchDeclined)
        self.assertEqual(result.suggested_tier, "llm-fallback")

    def test_command_injection_declines_llm_fallback(self):
        finding = _make_finding("command-injection", "security")
        result = command_injection.generate(finding, _SOURCE, _ROOT)
        self.assertIsInstance(result, PatchDeclined)
        self.assertEqual(result.suggested_tier, "llm-fallback")

    def test_cyclic_import_declines_human_only(self):
        finding = _make_finding("cyclic-import")
        result = cyclic_import.generate(finding, _SOURCE, _ROOT)
        self.assertIsInstance(result, PatchDeclined)
        self.assertEqual(result.suggested_tier, "human-only")
        self.assertEqual(result.reason_code, "cross-file-change")

    def test_duplicate_code_declines_llm_fallback(self):
        finding = _make_finding("duplicate-code")
        result = duplicate_code.generate(finding, _SOURCE, _ROOT)
        self.assertIsInstance(result, PatchDeclined)
        self.assertEqual(result.suggested_tier, "llm-fallback")

    def test_too_long_declines_llm_fallback(self):
        finding = _make_finding("too-long")
        result = too_long.generate(finding, _SOURCE, _ROOT)
        self.assertIsInstance(result, PatchDeclined)
        self.assertEqual(result.suggested_tier, "llm-fallback")

    def test_too_complex_declines_llm_fallback(self):
        finding = _make_finding("too-complex")
        result = too_complex.generate(finding, _SOURCE, _ROOT)
        self.assertIsInstance(result, PatchDeclined)
        self.assertEqual(result.suggested_tier, "llm-fallback")

    def test_shadowed_builtin_declines_llm_fallback(self):
        finding = _make_finding("shadowed-builtin")
        result = shadowed_builtin.generate(finding, _SOURCE, _ROOT)
        self.assertIsInstance(result, PatchDeclined)
        self.assertEqual(result.suggested_tier, "llm-fallback")

    def test_coverage_gap_declines_human_only(self):
        finding = _make_finding("coverage-gap")
        result = coverage_gap.generate(finding, _SOURCE, _ROOT)
        self.assertIsInstance(result, PatchDeclined)
        self.assertEqual(result.suggested_tier, "human-only")


class GeneratorRegistrationTests(unittest.TestCase):
    """Verify all 31 generators are registered in the dispatcher."""

    def test_31_generators_registered(self):
        from scripts.quality.rollup_v2.patches import GENERATORS
        self.assertEqual(len(GENERATORS), 31)

    def test_all_expected_categories_present(self):
        from scripts.quality.rollup_v2.patches import GENERATORS
        expected = {
            "assert-in-production", "bad-line-ending", "bare-raise",
            "broad-except", "command-injection", "coverage-gap",
            "cyclic-import", "dead-code", "duplicate-code",
            "hardcoded-secret", "indent-mismatch", "insecure-random",
            "line-too-long", "missing-docstring", "mutable-default",
            "naming-convention", "open-redirect", "print-in-production",
            "quote-style", "shadowed-builtin", "spacing-convention",
            "tab-vs-space", "todo-comment", "too-complex", "too-long",
            "trailing-newline", "trailing-whitespace", "unused-import",
            "unused-variable", "weak-crypto", "wrong-import-order",
        }
        self.assertEqual(set(GENERATORS.keys()), expected)

    def test_each_generator_has_generate_function(self):
        from scripts.quality.rollup_v2.patches import GENERATORS
        for category, module in GENERATORS.items():
            self.assertTrue(
                callable(getattr(module, "generate", None)),
                f"Generator {category} has no callable generate()",
            )


if __name__ == "__main__":
    unittest.main()
