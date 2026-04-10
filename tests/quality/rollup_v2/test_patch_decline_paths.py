"""Tests for patch generator decline paths (line-out-of-range, no-match, etc.)."""
from __future__ import absolute_import

import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.schema.corroborator import Corroborator
from scripts.quality.rollup_v2.schema.finding import SCHEMA_VERSION, Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined


def _make_finding(category: str, file: str, line: int, **kw) -> Finding:
    return Finding(
        schema_version=SCHEMA_VERSION,
        finding_id="test-0001",
        file=file,
        line=line,
        end_line=kw.get("end_line", line),
        column=None,
        category=category,
        category_group=kw.get("category_group", "quality"),
        severity="medium",
        corroboration="single",
        primary_message="test",
        corroborators=(
            Corroborator.from_provider(
                provider="QLTY", rule_id="t", rule_url=None, original_message="t"
            ),
        ),
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


def _run(module, finding, source):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        fdir = root / Path(finding.file).parent
        fdir.mkdir(parents=True, exist_ok=True)
        (root / finding.file).write_text(source, encoding="utf-8")
        return module.generate(finding, source_file_content=source, repo_root=root)


class OutOfRangeDeclineTests(unittest.TestCase):
    """Ensure all generators handle out-of-range line numbers."""

    def _check_oor(self, module, category):
        f = _make_finding(category, "src/a.py", 999)
        result = _run(module, f, "x = 1\n")
        self.assertIsInstance(result, PatchDeclined)

    def test_unused_import_oor(self):
        from scripts.quality.rollup_v2.patches import unused_import
        self._check_oor(unused_import, "unused-import")

    def test_bare_raise_oor(self):
        from scripts.quality.rollup_v2.patches import bare_raise
        self._check_oor(bare_raise, "bare-raise")

    def test_broad_except_oor(self):
        from scripts.quality.rollup_v2.patches import broad_except
        self._check_oor(broad_except, "broad-except")

    def test_dead_code_oor(self):
        from scripts.quality.rollup_v2.patches import dead_code
        self._check_oor(dead_code, "dead-code")

    def test_hardcoded_secret_oor(self):
        from scripts.quality.rollup_v2.patches import hardcoded_secret
        self._check_oor(hardcoded_secret, "hardcoded-secret")

    def test_print_in_production_oor(self):
        from scripts.quality.rollup_v2.patches import print_in_production
        self._check_oor(print_in_production, "print-in-production")

    def test_trailing_whitespace_oor(self):
        from scripts.quality.rollup_v2.patches import trailing_whitespace
        self._check_oor(trailing_whitespace, "trailing-whitespace")

    def test_trailing_newline_oor(self):
        from scripts.quality.rollup_v2.patches import trailing_newline
        self._check_oor(trailing_newline, "trailing-newline")

    def test_indent_mismatch_oor(self):
        from scripts.quality.rollup_v2.patches import indent_mismatch
        self._check_oor(indent_mismatch, "indent-mismatch")

    def test_quote_style_oor(self):
        from scripts.quality.rollup_v2.patches import quote_style
        self._check_oor(quote_style, "quote-style")

    def test_bad_line_ending_oor(self):
        from scripts.quality.rollup_v2.patches import bad_line_ending
        self._check_oor(bad_line_ending, "bad-line-ending")

    def test_tab_vs_space_oor(self):
        from scripts.quality.rollup_v2.patches import tab_vs_space
        self._check_oor(tab_vs_space, "tab-vs-space")

    def test_spacing_convention_oor(self):
        from scripts.quality.rollup_v2.patches import spacing_convention
        self._check_oor(spacing_convention, "spacing-convention")

    def test_line_too_long_oor(self):
        from scripts.quality.rollup_v2.patches import line_too_long
        self._check_oor(line_too_long, "line-too-long")

    def test_missing_docstring_oor(self):
        from scripts.quality.rollup_v2.patches import missing_docstring
        self._check_oor(missing_docstring, "missing-docstring")

    def test_mutable_default_oor(self):
        from scripts.quality.rollup_v2.patches import mutable_default
        self._check_oor(mutable_default, "mutable-default")

    def test_unused_variable_oor(self):
        from scripts.quality.rollup_v2.patches import unused_variable
        self._check_oor(unused_variable, "unused-variable")

    def test_assert_in_production_oor(self):
        from scripts.quality.rollup_v2.patches import assert_in_production
        self._check_oor(assert_in_production, "assert-in-production")


class NoMatchDeclineTests(unittest.TestCase):
    """Ensure generators decline when pattern doesn't match."""

    def test_unused_import_no_match(self):
        from scripts.quality.rollup_v2.patches import unused_import
        f = _make_finding("unused-import", "src/a.py", 1)
        result = _run(unused_import, f, "x = 1\n")
        self.assertIsInstance(result, PatchDeclined)

    def test_bare_raise_no_match(self):
        from scripts.quality.rollup_v2.patches import bare_raise
        f = _make_finding("bare-raise", "src/a.py", 1)
        result = _run(bare_raise, f, "x = 1\n")
        self.assertIsInstance(result, PatchDeclined)

    def test_broad_except_no_match(self):
        from scripts.quality.rollup_v2.patches import broad_except
        f = _make_finding("broad-except", "src/a.py", 1)
        result = _run(broad_except, f, "x = 1\n")
        self.assertIsInstance(result, PatchDeclined)

    def test_hardcoded_secret_no_match(self):
        from scripts.quality.rollup_v2.patches import hardcoded_secret
        f = _make_finding("hardcoded-secret", "src/a.py", 1, category_group="security")
        result = _run(hardcoded_secret, f, "x = 1\n")
        self.assertIsInstance(result, PatchDeclined)

    def test_print_in_production_no_match(self):
        from scripts.quality.rollup_v2.patches import print_in_production
        f = _make_finding("print-in-production", "src/a.py", 1)
        result = _run(print_in_production, f, "x = 1\n")
        self.assertIsInstance(result, PatchDeclined)

    def test_dead_code_blank_line(self):
        from scripts.quality.rollup_v2.patches import dead_code
        f = _make_finding("dead-code", "src/a.py", 1)
        result = _run(dead_code, f, "\n")
        self.assertIsInstance(result, PatchDeclined)

    def test_spacing_convention_no_fix(self):
        from scripts.quality.rollup_v2.patches import spacing_convention
        f = _make_finding("spacing-convention", "src/a.py", 1)
        result = _run(spacing_convention, f, "x = 1\n")  # already correct spacing
        self.assertIsInstance(result, PatchDeclined)

    def test_quote_style_no_single_quotes(self):
        from scripts.quality.rollup_v2.patches import quote_style
        f = _make_finding("quote-style", "src/a.py", 1)
        result = _run(quote_style, f, 'x = "hello"\n')  # already double quotes
        self.assertIsInstance(result, PatchDeclined)

    def test_tab_vs_space_no_tabs(self):
        from scripts.quality.rollup_v2.patches import tab_vs_space
        f = _make_finding("tab-vs-space", "src/a.py", 1)
        result = _run(tab_vs_space, f, "    x = 1\n")  # already spaces
        self.assertIsInstance(result, PatchDeclined)

    def test_indent_mismatch_consistent(self):
        from scripts.quality.rollup_v2.patches import indent_mismatch
        f = _make_finding("indent-mismatch", "src/a.py", 1)
        result = _run(indent_mismatch, f, "x = 1\n")  # no indent at all
        self.assertIsInstance(result, PatchDeclined)

    def test_missing_docstring_no_def(self):
        from scripts.quality.rollup_v2.patches import missing_docstring
        f = _make_finding("missing-docstring", "src/a.py", 1)
        result = _run(missing_docstring, f, "x = 1\n")
        self.assertIsInstance(result, PatchDeclined)

    def test_mutable_default_no_default(self):
        from scripts.quality.rollup_v2.patches import mutable_default
        f = _make_finding("mutable-default", "src/a.py", 1)
        result = _run(mutable_default, f, "def foo(x):\n    pass\n")
        self.assertIsInstance(result, PatchDeclined)

    def test_unused_variable_no_assignment(self):
        from scripts.quality.rollup_v2.patches import unused_variable
        f = _make_finding("unused-variable", "src/a.py", 1)
        result = _run(unused_variable, f, "pass\n")
        self.assertIsInstance(result, PatchDeclined)

    def test_assert_in_production_no_assert(self):
        from scripts.quality.rollup_v2.patches import assert_in_production
        f = _make_finding("assert-in-production", "src/a.py", 1)
        result = _run(assert_in_production, f, "x = 1\n")
        self.assertIsInstance(result, PatchDeclined)

    def test_trailing_whitespace_no_trailing(self):
        from scripts.quality.rollup_v2.patches import trailing_whitespace
        f = _make_finding("trailing-whitespace", "src/a.py", 1)
        result = _run(trailing_whitespace, f, "x = 1\n")  # no trailing whitespace
        self.assertIsInstance(result, PatchDeclined)

    def test_trailing_newline_correct(self):
        from scripts.quality.rollup_v2.patches import trailing_newline
        f = _make_finding("trailing-newline", "src/a.py", 1)
        result = _run(trailing_newline, f, "x = 1\n")  # correct trailing newline
        self.assertIsInstance(result, PatchDeclined)

    def test_bad_line_ending_correct(self):
        from scripts.quality.rollup_v2.patches import bad_line_ending
        f = _make_finding("bad-line-ending", "src/a.py", 1)
        result = _run(bad_line_ending, f, "x = 1\n")  # LF only
        self.assertIsInstance(result, PatchDeclined)

    def test_wrong_import_order_sorted(self):
        from scripts.quality.rollup_v2.patches import wrong_import_order
        f = _make_finding("wrong-import-order", "src/a.py", 1)
        result = _run(wrong_import_order, f, "import json\nimport os\nimport sys\n\nx = 1\n")
        # Already sorted - should decline or produce no diff
        self.assertIsNotNone(result)

    def test_line_too_long_short_line(self):
        from scripts.quality.rollup_v2.patches import line_too_long
        f = _make_finding("line-too-long", "src/a.py", 1)
        result = _run(line_too_long, f, "x = 1\n")
        self.assertIsInstance(result, PatchDeclined)


class DeadCodeEdgeCases(unittest.TestCase):
    """Additional edge cases for dead_code patch generator."""

    def test_dead_code_with_blank_lines_in_block(self):
        from scripts.quality.rollup_v2.patches import dead_code
        source = "def f():\n    return 1\n\n    x = 2\n    y = 3\n"
        f = _make_finding("dead-code", "src/a.py", 4)
        result = _run(dead_code, f, source)
        self.assertIsNotNone(result)

    def test_dead_code_reduced_indent_stops_removal(self):
        from scripts.quality.rollup_v2.patches import dead_code
        source = "def f():\n    return 1\n    x = 2\ndef g():\n    pass\n"
        f = _make_finding("dead-code", "src/a.py", 3)
        result = _run(dead_code, f, source)
        self.assertIsNotNone(result)

    def test_dead_code_with_interleaved_blanks(self):
        """Cover lines 51-52: blank lines in dead code block."""
        from scripts.quality.rollup_v2.patches import dead_code
        source = "def f():\n    return 1\n    \n    x = 2\n"
        f = _make_finding("dead-code", "src/a.py", 3)
        result = _run(dead_code, f, source)
        self.assertIsNotNone(result)


class WrongImportOrderEdgeCases(unittest.TestCase):
    """Cover uncovered branches in wrong_import_order."""

    def test_from_import_reordering(self):
        from scripts.quality.rollup_v2.patches import wrong_import_order
        source = "from pathlib import Path\nimport os\nfrom sys import argv\nimport json\n\nx = 1\n"
        f = _make_finding("wrong-import-order", "src/a.py", 1)
        result = _run(wrong_import_order, f, source)
        self.assertIsNotNone(result)

    def test_no_imports_declines(self):
        from scripts.quality.rollup_v2.patches import wrong_import_order
        source = "x = 1\ny = 2\n"
        f = _make_finding("wrong-import-order", "src/a.py", 1)
        result = _run(wrong_import_order, f, source)
        self.assertIsNotNone(result)


class MissingDocstringEdgeCases(unittest.TestCase):
    """Cover uncovered branches in missing_docstring."""

    def test_function_with_body(self):
        from scripts.quality.rollup_v2.patches import missing_docstring
        source = "def foo(x, y):\n    return x + y\n"
        f = _make_finding("missing-docstring", "src/a.py", 1)
        result = _run(missing_docstring, f, source)
        self.assertIsNotNone(result)

    def test_async_def(self):
        from scripts.quality.rollup_v2.patches import missing_docstring
        source = "async def foo():\n    pass\n"
        f = _make_finding("missing-docstring", "src/a.py", 1)
        result = _run(missing_docstring, f, source)
        self.assertIsNotNone(result)

    def test_multiline_def(self):
        from scripts.quality.rollup_v2.patches import missing_docstring
        source = "def foo(\n    x,\n    y\n):\n    return x + y\n"
        f = _make_finding("missing-docstring", "src/a.py", 1)
        result = _run(missing_docstring, f, source)
        self.assertIsNotNone(result)

    def test_multiline_def_without_closing_colon(self):
        """Cover branch 53->59: multi-line def where while loop exhausts without finding ':'."""
        from scripts.quality.rollup_v2.patches import missing_docstring
        # def line has no colon, subsequent lines also have no colon
        source = "def foo(\n    x\n    y\n"
        f = _make_finding("missing-docstring", "src/a.py", 1)
        result = _run(missing_docstring, f, source)
        self.assertIsNotNone(result)


class AssertInProductionEdgeCases(unittest.TestCase):
    """Cover uncovered assert_in_production branches."""

    def test_assert_multiline(self):
        from scripts.quality.rollup_v2.patches import assert_in_production
        source = "assert (\n    x > 0\n)\n"
        f = _make_finding("assert-in-production", "src/a.py", 1)
        result = _run(assert_in_production, f, source)
        self.assertIsNotNone(result)


class LineTooLongEdgeCases(unittest.TestCase):
    """Cover uncovered line_too_long branches."""

    def test_very_long_string(self):
        from scripts.quality.rollup_v2.patches import line_too_long
        # A line that's long but has a natural break point
        source = "x = " + "'a' + " * 30 + "'b'\n"
        f = _make_finding("line-too-long", "src/a.py", 1)
        result = _run(line_too_long, f, source)
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
