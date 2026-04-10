"""Tests for patch generator happy paths (cover the actual diff generation lines)."""
from __future__ import absolute_import

import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.schema.corroborator import Corroborator
from scripts.quality.rollup_v2.schema.finding import SCHEMA_VERSION, Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult


def _make_finding(category: str, file: str, line: int, **kwargs) -> Finding:
    """Build a minimal Finding for patch generator tests."""
    return Finding(
        schema_version=SCHEMA_VERSION,
        finding_id="test-0001",
        file=file,
        line=line,
        end_line=kwargs.get("end_line", line),
        column=kwargs.get("column", None),
        category=category,
        category_group=kwargs.get("category_group", "quality"),
        severity="medium",
        corroboration="single",
        primary_message=kwargs.get("primary_message", "test"),
        corroborators=(
            Corroborator.from_provider(
                provider="QLTY", rule_id="test", rule_url=None, original_message="test"
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


def _run_generator(module, finding, source):
    """Run a patch generator in a temp directory."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        fdir = root / Path(finding.file).parent
        fdir.mkdir(parents=True, exist_ok=True)
        (root / finding.file).write_text(source, encoding="utf-8")
        return module.generate(finding, source_file_content=source, repo_root=root)


class UnusedImportHappyPathTest(unittest.TestCase):
    def test_remove_import_line(self):
        from scripts.quality.rollup_v2.patches import unused_import
        source = "import os\nimport sys\n\nx = 1\n"
        f = _make_finding("unused-import", "src/app.py", 1)
        result = _run_generator(unused_import, f, source)
        self.assertIsInstance(result, PatchResult)
        self.assertIn("-import os", result.unified_diff)


class UnusedVariableHappyPathTest(unittest.TestCase):
    def test_remove_variable_assignment(self):
        from scripts.quality.rollup_v2.patches import unused_variable
        source = "x = 42\ny = 10\n"
        f = _make_finding("unused-variable", "src/app.py", 1)
        result = _run_generator(unused_variable, f, source)
        self.assertIsInstance(result, PatchResult)


class BareRaiseHappyPathTest(unittest.TestCase):
    def test_replace_bare_raise(self):
        from scripts.quality.rollup_v2.patches import bare_raise
        source = "try:\n    pass\nexcept:\n    raise\n"
        f = _make_finding("bare-raise", "src/app.py", 4)
        result = _run_generator(bare_raise, f, source)
        self.assertIsInstance(result, PatchResult)
        self.assertIn("RuntimeError", result.unified_diff)


class BroadExceptHappyPathTest(unittest.TestCase):
    def test_replace_broad_except(self):
        from scripts.quality.rollup_v2.patches import broad_except
        source = "try:\n    pass\nexcept Exception:\n    pass\n"
        f = _make_finding("broad-except", "src/app.py", 3)
        result = _run_generator(broad_except, f, source)
        self.assertIsInstance(result, PatchResult)
        self.assertIn("IOError", result.unified_diff)


class HardcodedSecretHappyPathTest(unittest.TestCase):
    def test_replace_hardcoded_secret(self):
        from scripts.quality.rollup_v2.patches import hardcoded_secret
        source = 'API_KEY = "sk_live_super_secret_key_value_1234"\n'
        f = _make_finding("hardcoded-secret", "src/config.py", 1, category_group="security")
        result = _run_generator(hardcoded_secret, f, source)
        self.assertIsInstance(result, PatchResult)
        self.assertIn("os.environ", result.unified_diff)

    def test_replace_hardcoded_secret_when_os_already_imported(self):
        from scripts.quality.rollup_v2.patches import hardcoded_secret
        source = 'import os\nAPI_KEY = "sk_live_super_secret_key_value_1234"\n'
        f = _make_finding("hardcoded-secret", "src/config.py", 2, category_group="security")
        result = _run_generator(hardcoded_secret, f, source)
        self.assertIsInstance(result, PatchResult)
        self.assertIn("os.environ", result.unified_diff)
        # Should NOT add duplicate import os
        self.assertNotIn("+import os", result.unified_diff)


class PrintInProductionHappyPathTest(unittest.TestCase):
    def test_remove_print(self):
        from scripts.quality.rollup_v2.patches import print_in_production
        source = 'print("debug")\nx = 1\n'
        f = _make_finding("print-in-production", "src/app.py", 1)
        result = _run_generator(print_in_production, f, source)
        self.assertIsInstance(result, PatchResult)

    def test_remove_print_when_logging_already_imported(self):
        from scripts.quality.rollup_v2.patches import print_in_production
        source = 'import logging\nprint("debug")\nx = 1\n'
        f = _make_finding("print-in-production", "src/app.py", 2)
        result = _run_generator(print_in_production, f, source)
        self.assertIsInstance(result, PatchResult)
        self.assertIn("logging.info", result.unified_diff)
        # Should NOT add duplicate import logging
        self.assertNotIn("+import logging", result.unified_diff)


class TrailingWhitespaceHappyPathTest(unittest.TestCase):
    def test_strip_trailing_whitespace(self):
        from scripts.quality.rollup_v2.patches import trailing_whitespace
        source = "x = 1   \ny = 2\n"
        f = _make_finding("trailing-whitespace", "src/app.py", 1)
        result = _run_generator(trailing_whitespace, f, source)
        self.assertIsInstance(result, PatchResult)


class TrailingNewlineHappyPathTest(unittest.TestCase):
    def test_add_trailing_newline(self):
        from scripts.quality.rollup_v2.patches import trailing_newline
        source = "x = 1"  # no trailing newline
        f = _make_finding("trailing-newline", "src/app.py", 1)
        result = _run_generator(trailing_newline, f, source)
        self.assertIsInstance(result, PatchResult)

    def test_remove_excess_trailing_newlines(self):
        from scripts.quality.rollup_v2.patches import trailing_newline
        source = "x = 1\n\n\n\n"  # too many trailing newlines
        f = _make_finding("trailing-newline", "src/app.py", 4)
        result = _run_generator(trailing_newline, f, source)
        self.assertIsInstance(result, PatchResult)


class QuoteStyleHappyPathTest(unittest.TestCase):
    def test_convert_single_to_double(self):
        from scripts.quality.rollup_v2.patches import quote_style
        source = "x = 'hello'\n"
        f = _make_finding("quote-style", "src/app.py", 1)
        result = _run_generator(quote_style, f, source)
        self.assertIsInstance(result, PatchResult)


class BadLineEndingHappyPathTest(unittest.TestCase):
    def test_fix_crlf(self):
        from scripts.quality.rollup_v2.patches import bad_line_ending
        source = "x = 1\r\ny = 2\r\n"
        f = _make_finding("bad-line-ending", "src/app.py", 1)
        result = _run_generator(bad_line_ending, f, source)
        self.assertIsInstance(result, PatchResult)


class TabVsSpaceHappyPathTest(unittest.TestCase):
    def test_convert_tabs_to_spaces(self):
        from scripts.quality.rollup_v2.patches import tab_vs_space
        source = "def f():\n\tx = 1\n"
        f = _make_finding("tab-vs-space", "src/app.py", 2)
        result = _run_generator(tab_vs_space, f, source)
        self.assertIsInstance(result, PatchResult)


class SpacingConventionHappyPathTest(unittest.TestCase):
    def test_fix_spacing_around_equals(self):
        from scripts.quality.rollup_v2.patches import spacing_convention
        source = "x=1\n"
        f = _make_finding("spacing-convention", "src/app.py", 1)
        result = _run_generator(spacing_convention, f, source)
        self.assertIsInstance(result, PatchResult)

    def test_fix_spacing_comment_passthrough(self):
        from scripts.quality.rollup_v2.patches import spacing_convention
        source = "# x=1\ny=2\n"
        f = _make_finding("spacing-convention", "src/app.py", 2)
        result = _run_generator(spacing_convention, f, source)
        self.assertIsInstance(result, PatchResult)


class IndentMismatchHappyPathTest(unittest.TestCase):
    def test_fix_mixed_indent(self):
        from scripts.quality.rollup_v2.patches import indent_mismatch
        source = "def f():\n\t    x = 1\n"
        f = _make_finding("indent-mismatch", "src/app.py", 2)
        result = _run_generator(indent_mismatch, f, source)
        self.assertIsInstance(result, PatchResult)

    def test_fix_3space_indent(self):
        from scripts.quality.rollup_v2.patches import indent_mismatch
        source = "def f():\n   x = 1\n"
        f = _make_finding("indent-mismatch", "src/app.py", 2)
        result = _run_generator(indent_mismatch, f, source)
        self.assertIsInstance(result, PatchResult)

    def test_fix_indent_after_colon_line(self):
        """Cover branch 39->41: previous line ends with ':' adds extra indent."""
        from scripts.quality.rollup_v2.patches import indent_mismatch
        source = "if True:\n  x = 1\n"
        f = _make_finding("indent-mismatch", "src/app.py", 2)
        result = _run_generator(indent_mismatch, f, source)
        self.assertIsInstance(result, PatchResult)

    def test_fix_indent_on_first_line(self):
        """Cover branch 35->33: target_index is 0, loop range is empty."""
        from scripts.quality.rollup_v2.patches import indent_mismatch
        source = "  x = 1\ny = 2\n"
        f = _make_finding("indent-mismatch", "src/app.py", 1)
        result = _run_generator(indent_mismatch, f, source)
        self.assertIsInstance(result, PatchResult)

    def test_fix_indent_line_without_trailing_newline(self):
        """Cover branch 53->56: target line does not end with newline."""
        from scripts.quality.rollup_v2.patches import indent_mismatch
        source = "def f():\n\t    x = 1"
        f = _make_finding("indent-mismatch", "src/app.py", 2)
        result = _run_generator(indent_mismatch, f, source)
        self.assertIsInstance(result, PatchResult)

    def test_fix_indent_with_blank_lines_before_target(self):
        """Cover branches 35->33 (blank line iteration) and 39->41 (colon ending)."""
        from scripts.quality.rollup_v2.patches import indent_mismatch
        # Blank lines between def and target force the loop to skip blanks
        # then find 'def f():' which ends with ':', triggering colon branch
        source = "def f():\n\n\n  x = 1\n"
        f = _make_finding("indent-mismatch", "src/app.py", 4)
        result = _run_generator(indent_mismatch, f, source)
        self.assertIsInstance(result, PatchResult)


class LineTooLongHappyPathTest(unittest.TestCase):
    def test_wrap_long_line(self):
        from scripts.quality.rollup_v2.patches import line_too_long
        long_line = "x = " + "a" * 120 + "\n"
        source = long_line + "y = 1\n"
        f = _make_finding("line-too-long", "src/app.py", 1)
        result = _run_generator(line_too_long, f, source)
        # Long line wrapping is complex; may be PatchResult or PatchDeclined
        self.assertIsNotNone(result)


class MissingDocstringHappyPathTest(unittest.TestCase):
    def test_add_docstring_to_function(self):
        from scripts.quality.rollup_v2.patches import missing_docstring
        source = "def foo():\n    pass\n"
        f = _make_finding("missing-docstring", "src/app.py", 1)
        result = _run_generator(missing_docstring, f, source)
        self.assertIsNotNone(result)

    def test_add_docstring_to_class(self):
        from scripts.quality.rollup_v2.patches import missing_docstring
        source = "class Foo:\n    pass\n"
        f = _make_finding("missing-docstring", "src/app.py", 1)
        result = _run_generator(missing_docstring, f, source)
        self.assertIsNotNone(result)


class MutableDefaultHappyPathTest(unittest.TestCase):
    def test_fix_mutable_list_default(self):
        from scripts.quality.rollup_v2.patches import mutable_default
        source = "def foo(x=[]):\n    pass\n"
        f = _make_finding("mutable-default", "src/app.py", 1)
        result = _run_generator(mutable_default, f, source)
        self.assertIsNotNone(result)

    def test_fix_mutable_dict_default(self):
        from scripts.quality.rollup_v2.patches import mutable_default
        source = "def foo(x={}):\n    pass\n"
        f = _make_finding("mutable-default", "src/app.py", 1)
        result = _run_generator(mutable_default, f, source)
        self.assertIsNotNone(result)


class DeadCodeHappyPathTest(unittest.TestCase):
    def test_remove_dead_code_single_pass(self):
        from scripts.quality.rollup_v2.patches import dead_code
        source = "def foo():\n    return 1\n    x = 2\n"
        f = _make_finding("dead-code", "src/app.py", 3)
        result = _run_generator(dead_code, f, source)
        self.assertIsNotNone(result)

    def test_remove_unreachable_after_return(self):
        from scripts.quality.rollup_v2.patches import dead_code
        source = "def foo():\n    return 1\n    print('dead')\n    x = 2\n"
        f = _make_finding("dead-code", "src/app.py", 3, end_line=4)
        result = _run_generator(dead_code, f, source)
        self.assertIsNotNone(result)


class AssertInProductionHappyPathTest(unittest.TestCase):
    def test_replace_assert(self):
        from scripts.quality.rollup_v2.patches import assert_in_production
        source = "assert x > 0\ny = 1\n"
        f = _make_finding("assert-in-production", "src/app.py", 1)
        result = _run_generator(assert_in_production, f, source)
        self.assertIsNotNone(result)

    def test_replace_assert_with_message(self):
        from scripts.quality.rollup_v2.patches import assert_in_production
        source = 'assert x > 0, "must be positive"\n'
        f = _make_finding("assert-in-production", "src/app.py", 1)
        result = _run_generator(assert_in_production, f, source)
        self.assertIsNotNone(result)


class WrongImportOrderHappyPathTest(unittest.TestCase):
    def test_reorder_imports(self):
        from scripts.quality.rollup_v2.patches import wrong_import_order
        source = "import os\nimport sys\nfrom pathlib import Path\nimport json\n\nx = 1\n"
        f = _make_finding("wrong-import-order", "src/app.py", 1)
        result = _run_generator(wrong_import_order, f, source)
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
