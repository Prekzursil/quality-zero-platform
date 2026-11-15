"""Tests for the gate-7 complexity floor (T1 new-code-only tiering).

``scripts/quality/complexity_gate.py`` reads a ``lizard --csv`` report plus the
unified diff of the pull request and splits the over-threshold functions into

* **BLOCK** - the diff added or changed lines *inside* the function's own span, and
* **T2** - everything else, which is still PRINTED (demoted, never hidden).

The scope is deliberately LINE-level, not file-level. ``tests/fixtures/newcode/``
was recorded to make that distinction fail loudly if it is ever weakened:
``src/legacy_knot.py`` **is** a changed file, but the only hunk in it adds lines
48-52, while the CCN=21 ``classify`` occupies lines 4-42. A file-level scope
would block the PR on a legacy function it never touched - the exact treadmill
the charter exists to avoid - so ``classify`` must land in T2 while the
newly-added Go ``Knot`` (CCN=18) blocks.

Both lizard fixtures are real ``lizard --csv`` output. They deliberately carry
two different path conventions: ``lizard_mixed.csv`` holds the Windows form
(``.\\src\\legacy_knot.py``, as recorded) and ``lizard_clean.csv`` the POSIX form
(``src/clean.py``), because the recording ran on Windows and CI runs on Linux.
"""

from __future__ import absolute_import

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import List, Tuple

from tests.newcode_scope_support import NewCodeScopeContract

from scripts.quality import complexity_gate as gate

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "newcode"
MIXED_CSV = FIXTURES / "lizard_mixed.csv"
CLEAN_CSV = FIXTURES / "lizard_clean.csv"
NEWCODE_DIFF = FIXTURES / "newcode.diff"


def _run(argv: List[str]) -> Tuple[int, str, str]:
    """Invoke ``main`` and capture the exit code with both streams, kept APART.

    Deliberately not the ``_run`` helpers in ``test_osv_severity_gate.py`` (which
    concatenates the two streams) or ``test_apply_drift_pr.py`` (which returns a
    call log): the passing path here asserts that stderr is *empty*, which a
    concatenating helper cannot express.
    """
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = gate.main(argv)
    return code, out.getvalue(), err.getvalue()


class ComplexityScopeContractTests(NewCodeScopeContract, unittest.TestCase):
    """Run the shared new-code scoping contract against THIS module's copy."""

    module = gate


class ParseLizardCsvTests(unittest.TestCase):
    """The CSV parser must read real lizard output, header-less and quoted."""

    def test_real_mixed_report_yields_every_function(self) -> None:
        """The recorded report holds nine functions across Python and Go."""
        report = gate.parse_lizard_csv(MIXED_CSV.read_text(encoding="utf-8"))
        self.assertEqual(len(report.functions), 9)
        self.assertEqual(report.skipped_rows, 0)

    def test_the_recorded_ccn_values_are_read_verbatim(self) -> None:
        """These are the numbers the whole gate turns on; pin them."""
        report = gate.parse_lizard_csv(MIXED_CSV.read_text(encoding="utf-8"))
        by_name = {function.name: function for function in report.functions}
        self.assertEqual(by_name["classify"].ccn, 21)
        self.assertEqual(by_name["Knot"].ccn, 18)
        self.assertEqual(by_name["load_settings"].ccn, 8)
        self.assertEqual(by_name["read_config"].ccn, 8)
        self.assertEqual(by_name["ok"].ccn, 1)

    def test_spans_are_read_from_the_start_and_end_columns(self) -> None:
        """Line-level scoping is only possible because lizard reports the span."""
        report = gate.parse_lizard_csv(MIXED_CSV.read_text(encoding="utf-8"))
        by_name = {function.name: function for function in report.functions}
        self.assertEqual((by_name["classify"].start, by_name["classify"].end), (4, 42))
        self.assertEqual((by_name["also_ok"].start, by_name["also_ok"].end), (50, 52))
        self.assertEqual((by_name["Knot"].start, by_name["Knot"].end), (13, 59))

    def test_paths_are_normalized_so_diff_keys_match(self) -> None:
        """The recorded Windows paths must arrive as repo-relative POSIX keys."""
        report = gate.parse_lizard_csv(MIXED_CSV.read_text(encoding="utf-8"))
        self.assertEqual(
            sorted({function.path for function in report.functions}),
            ["src/clean.py", "src/legacy_knot.py", "src/legacy_settings.py", "src/new_knot.go", "src/new_settings.py"],
        )

    def test_a_signature_containing_commas_does_not_shift_columns(self) -> None:
        """``classify( value , mode , ... )`` is quoted; naive splitting would misread it."""
        report = gate.parse_lizard_csv(MIXED_CSV.read_text(encoding="utf-8"))
        by_name = {function.name: function for function in report.functions}
        self.assertEqual(by_name["classify"].path, "src/legacy_knot.py")
        self.assertEqual(by_name["classify"].ccn, 21)

    def test_clean_report_yields_one_trivial_function(self) -> None:
        """The clean fixture is the PASS side of the both-states pair."""
        report = gate.parse_lizard_csv(CLEAN_CSV.read_text(encoding="utf-8"))
        self.assertEqual([(f.name, f.ccn, f.path) for f in report.functions], [("ok", 1, "src/clean.py")])

    def test_empty_report_yields_no_functions(self) -> None:
        """lizard on a tree with no functions emits an empty file."""
        report = gate.parse_lizard_csv("")
        self.assertEqual(report.functions, [])
        self.assertEqual(report.skipped_rows, 0)

    def test_blank_lines_are_not_counted_as_skipped_rows(self) -> None:
        """The recorded files end with a newline; that is not a malformed row."""
        report = gate.parse_lizard_csv("\n\n")
        self.assertEqual(report.skipped_rows, 0)

    def test_a_short_row_is_counted_as_skipped_not_silently_dropped(self) -> None:
        """A truncated row means the report is suspect and must be reported."""
        report = gate.parse_lizard_csv("1,2,3\n")
        self.assertEqual(report.functions, [])
        self.assertEqual(report.skipped_rows, 1)

    def test_a_header_row_is_counted_as_skipped(self) -> None:
        """lizard --csv emits no header, but a hand-made file might."""
        header = "NLOC,CCN,token_count,param_count,length,name,file,function,signature,start,end\n"
        report = gate.parse_lizard_csv(header)
        self.assertEqual(report.functions, [])
        self.assertEqual(report.skipped_rows, 1)

    def test_a_row_with_a_non_numeric_span_is_skipped(self) -> None:
        """Only the CCN and the span are load-bearing; both must be integers."""
        row = '2,1,10,1,3,"ok@1-3@src/x.py","src/x.py","ok","ok( value )",one,3\n'
        report = gate.parse_lizard_csv(row)
        self.assertEqual(report.functions, [])
        self.assertEqual(report.skipped_rows, 1)

    def test_crlf_line_endings_parse_identically(self) -> None:
        """The fixtures are stored LF but checked out CRLF on Windows."""
        text = CLEAN_CSV.read_text(encoding="utf-8").replace("\n", "\r\n")
        report = gate.parse_lizard_csv(text)
        self.assertEqual([f.name for f in report.functions], ["ok"])
        self.assertEqual(report.skipped_rows, 0)


class ClassifyTests(unittest.TestCase):
    """The tiering decision, driven by the real recorded report."""

    def setUp(self) -> None:
        """Load the recorded report and diff once per test."""
        self.functions = gate.parse_lizard_csv(MIXED_CSV.read_text(encoding="utf-8")).functions
        self.ranges = gate.parse_added_ranges(NEWCODE_DIFF.read_text(encoding="utf-8"))

    def test_new_over_threshold_code_blocks(self) -> None:
        """``Knot`` (CCN=18) is a brand-new Go function; it must block."""
        verdict = gate.classify(self.functions, self.ranges, gate.DEFAULT_MAX_CCN, scoped=True)
        self.assertEqual([function.name for function in verdict.blocking], ["Knot"])

    def test_untouched_legacy_code_in_a_changed_file_is_demoted(self) -> None:
        """THE crux: ``classify`` (CCN=21) lives in a changed FILE but untouched LINES."""
        verdict = gate.classify(self.functions, self.ranges, gate.DEFAULT_MAX_CCN, scoped=True)
        self.assertEqual([function.name for function in verdict.inventory], ["classify"])
        self.assertNotIn("classify", [function.name for function in verdict.blocking])

    def test_under_threshold_functions_appear_in_neither_tier(self) -> None:
        """CCN=8 is well inside the bar; it is not debt and must not be listed."""
        verdict = gate.classify(self.functions, self.ranges, gate.DEFAULT_MAX_CCN, scoped=True)
        listed = [f.name for f in verdict.blocking] + [f.name for f in verdict.inventory]
        self.assertNotIn("read_config", listed)
        self.assertNotIn("load_settings", listed)
        self.assertNotIn("also_ok", listed)

    def test_unscoped_mode_demotes_everything(self) -> None:
        """A push to main has no diff base, so nothing may block."""
        verdict = gate.classify(self.functions, {}, gate.DEFAULT_MAX_CCN, scoped=False)
        self.assertEqual(verdict.blocking, [])
        self.assertEqual(sorted(f.name for f in verdict.inventory), ["Knot", "classify"])

    def test_a_clean_report_produces_an_empty_verdict(self) -> None:
        """The PASS side: nothing over threshold anywhere."""
        clean = gate.parse_lizard_csv(CLEAN_CSV.read_text(encoding="utf-8")).functions
        verdict = gate.classify(clean, self.ranges, gate.DEFAULT_MAX_CCN, scoped=True)
        self.assertEqual(verdict.blocking, [])
        self.assertEqual(verdict.inventory, [])

    def test_lowering_the_threshold_pulls_more_functions_in(self) -> None:
        """DETECTOR CONTROL: the threshold must actually be the comparison."""
        verdict = gate.classify(self.functions, self.ranges, 7, scoped=True)
        self.assertEqual(sorted(f.name for f in verdict.blocking), ["Knot", "read_config"])

    def test_raising_the_threshold_above_the_worst_function_clears_it(self) -> None:
        """The inverse control, so the comparison cannot be hardcoded."""
        verdict = gate.classify(self.functions, self.ranges, 21, scoped=True)
        self.assertEqual(verdict.blocking, [])
        self.assertEqual(verdict.inventory, [])


class ParseMaxCcnTests(unittest.TestCase):
    """A threshold that cannot express a silent pass."""

    def test_the_default_is_the_lizard_default(self) -> None:
        """15 is lizard's own ``-C`` default and the estate's routing-table value."""
        self.assertEqual(gate.DEFAULT_MAX_CCN, 15)

    def test_a_plain_integer_is_accepted(self) -> None:
        """The ordinary case."""
        self.assertEqual(gate.parse_max_ccn("10"), 10)

    def test_whitespace_is_tolerated(self) -> None:
        """A workflow input can arrive padded."""
        self.assertEqual(gate.parse_max_ccn(" 12 "), 12)

    def test_zero_is_rejected(self) -> None:
        """CCN is at least 1 for any real function, so 0 would block everything."""
        with self.assertRaises(gate.ThresholdError):
            gate.parse_max_ccn("0")

    def test_a_negative_threshold_is_rejected(self) -> None:
        """Nonsense input must be an error, not a silently inverted gate."""
        with self.assertRaises(gate.ThresholdError):
            gate.parse_max_ccn("-1")

    def test_a_threshold_above_the_accepted_ceiling_is_rejected(self) -> None:
        """An absurd bar is a switched-off gate wearing a threshold's clothes."""
        with self.assertRaises(gate.ThresholdError):
            gate.parse_max_ccn(str(gate.MAX_ACCEPTED_CCN + 1))

    def test_the_ceiling_itself_is_accepted(self) -> None:
        """The bound is inclusive; pin which side it falls on."""
        self.assertEqual(gate.parse_max_ccn(str(gate.MAX_ACCEPTED_CCN)), gate.MAX_ACCEPTED_CCN)

    def test_a_non_numeric_threshold_is_rejected(self) -> None:
        """``--max-ccn high`` must not be read as "no limit"."""
        with self.assertRaises(gate.ThresholdError):
            gate.parse_max_ccn("high")

    def test_a_float_threshold_is_rejected(self) -> None:
        """CCN is a count; ``15.5`` means the caller misunderstood the knob."""
        with self.assertRaises(gate.ThresholdError):
            gate.parse_max_ccn("15.5")


class RenderTests(unittest.TestCase):
    """Demoted is not hidden: the T2 set must be printed in full."""

    def setUp(self) -> None:
        """Load the recorded report and diff once per test."""
        self.report = gate.parse_lizard_csv(MIXED_CSV.read_text(encoding="utf-8"))
        self.ranges = gate.parse_added_ranges(NEWCODE_DIFF.read_text(encoding="utf-8"))

    def test_the_demoted_function_is_named_with_its_span(self) -> None:
        """A T2 row has to be actionable later, so it carries file, name and lines."""
        verdict = gate.classify(self.report.functions, self.ranges, gate.DEFAULT_MAX_CCN, scoped=True)
        text = gate.render_report(self.report, verdict, gate.DEFAULT_MAX_CCN)
        self.assertIn("src/legacy_knot.py", text)
        self.assertIn("classify", text)
        self.assertIn("4-42", text)
        self.assertIn("21", text)

    def test_the_blocking_function_is_named(self) -> None:
        """The blocking row must name the function the author has to simplify."""
        verdict = gate.classify(self.report.functions, self.ranges, gate.DEFAULT_MAX_CCN, scoped=True)
        text = gate.render_report(self.report, verdict, gate.DEFAULT_MAX_CCN)
        self.assertIn("Knot", text)
        self.assertIn("src/new_knot.go", text)

    def test_an_empty_verdict_says_so_explicitly(self) -> None:
        """Silence would be indistinguishable from a gate that did not run."""
        clean = gate.parse_lizard_csv(CLEAN_CSV.read_text(encoding="utf-8"))
        verdict = gate.classify(clean.functions, self.ranges, gate.DEFAULT_MAX_CCN, scoped=True)
        text = gate.render_report(clean, verdict, gate.DEFAULT_MAX_CCN)
        self.assertIn("1 function", text)
        self.assertIn("0 over", text)

    def test_unscoped_mode_is_labelled_as_inventory_only(self) -> None:
        """The reader must be able to tell a T2-only run from a scoped one."""
        verdict = gate.classify(self.report.functions, {}, gate.DEFAULT_MAX_CCN, scoped=False)
        text = gate.render_report(self.report, verdict, gate.DEFAULT_MAX_CCN)
        self.assertIn("inventory only", text)

    def test_skipped_rows_are_disclosed(self) -> None:
        """A partially-unreadable report must not look like a clean one."""
        report = gate.parse_lizard_csv("1,2,3\n")
        verdict = gate.classify(report.functions, {}, gate.DEFAULT_MAX_CCN, scoped=False)
        self.assertIn("1 unreadable row", gate.render_report(report, verdict, gate.DEFAULT_MAX_CCN))

    def test_a_fully_readable_report_does_not_mention_skipped_rows(self) -> None:
        """The disclosure must be conditional, or it is noise."""
        verdict = gate.classify(self.report.functions, {}, gate.DEFAULT_MAX_CCN, scoped=False)
        self.assertNotIn("unreadable row", gate.render_report(self.report, verdict, gate.DEFAULT_MAX_CCN))


class MainTests(unittest.TestCase):
    """End-to-end: the four both-states directions, through the real CLI."""

    def test_new_over_threshold_code_exits_blocked(self) -> None:
        """BOTH-STATES 1/4: an over-threshold new function FAILS the lane."""
        code, out, err = _run(["--csv", str(MIXED_CSV), "--diff", str(NEWCODE_DIFF)])
        self.assertEqual(code, gate.EXIT_BLOCKED)
        self.assertIn("FAIL gate-complexity", err)
        self.assertIn("Knot", out)

    def test_clean_new_code_exits_ok(self) -> None:
        """BOTH-STATES 2/4: a clean report PASSES the lane."""
        code, out, err = _run(["--csv", str(CLEAN_CSV), "--diff", str(NEWCODE_DIFF)])
        self.assertEqual(code, gate.EXIT_OK)
        self.assertIn("PASS gate-complexity", out)
        self.assertEqual(err, "")

    def test_the_demoted_set_is_printed_on_the_passing_path(self) -> None:
        """T2 must be visible even when the gate is green."""
        code, out, _ = _run(["--csv", str(MIXED_CSV), "--max-ccn", "21"])
        self.assertEqual(code, gate.EXIT_OK)
        self.assertIn("PASS gate-complexity", out)

    def test_without_a_diff_the_gate_never_blocks(self) -> None:
        """A push to main has no base; it emits inventory and passes."""
        code, out, err = _run(["--csv", str(MIXED_CSV)])
        self.assertEqual(code, gate.EXIT_OK)
        self.assertIn("inventory only", out)
        self.assertIn("classify", out)
        self.assertIn("Knot", out)
        self.assertEqual(err, "")

    def test_an_explicit_threshold_is_honoured(self) -> None:
        """DETECTOR CONTROL through the CLI, not just the pure function."""
        code, out, _ = _run(["--csv", str(MIXED_CSV), "--diff", str(NEWCODE_DIFF), "--max-ccn", "7"])
        self.assertEqual(code, gate.EXIT_BLOCKED)
        self.assertIn("read_config", out)

    def test_a_bad_threshold_is_a_config_error_not_a_pass(self) -> None:
        """Exit 2 is distinguishable from both 0 and 1 by the workflow."""
        code, _, err = _run(["--csv", str(MIXED_CSV), "--max-ccn", "0"])
        self.assertEqual(code, gate.EXIT_CONFIG_ERROR)
        self.assertIn("ERROR gate-complexity", err)

    def test_a_missing_csv_is_a_config_error(self) -> None:
        """An unmeasured tree must never be reported as a clean one."""
        code, _, err = _run(["--csv", str(FIXTURES / "does_not_exist.csv")])
        self.assertEqual(code, gate.EXIT_CONFIG_ERROR)
        self.assertIn("ERROR gate-complexity", err)

    def test_a_missing_diff_file_is_a_config_error(self) -> None:
        """If the workflow promised a diff, an unreadable one is not "no scope"."""
        code, _, err = _run(["--csv", str(MIXED_CSV), "--diff", str(FIXTURES / "nope.diff")])
        self.assertEqual(code, gate.EXIT_CONFIG_ERROR)
        self.assertIn("ERROR gate-complexity", err)

    def test_the_root_option_relativizes_absolute_paths(self) -> None:
        """CI can invoke lizard in a way that records absolute paths."""
        code, out, _ = _run(["--csv", str(MIXED_CSV), "--root", str(ROOT)])
        self.assertEqual(code, gate.EXIT_OK)
        self.assertIn("src/legacy_knot.py", out)


if __name__ == "__main__":
    unittest.main()
