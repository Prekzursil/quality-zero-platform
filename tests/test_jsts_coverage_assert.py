"""Tests for the gate-3 JS/TS coverage assertion.

The jsts lane used to run the caller's coverage script and pass on exit 0. It
passed NO threshold and checked NO number: enforcement was delegated entirely to
the caller's vitest/karma config, and the gate never verified that one existed.
Measured 2026-08-11 on momentstudio's frontend: the run printed
``Statements : 49.57%`` and the gate printed ``PASS gate-tests-coverage jsts``.

``scripts/quality/jsts_coverage_assert.py`` reads the summary the run just
produced and compares it to a number. The property that must hold regardless of
any threshold setting: **a run that produces no parseable coverage summary
FAILS.** It can never pass unmeasured.
"""

from __future__ import absolute_import

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional, Tuple

from scripts.quality import jsts_coverage_assert as assertion

TEXT_SUMMARY = """
=============================== Coverage summary ===============================
Statements   : 49.57% ( 2313/4666 )
Branches     : 30.12% ( 100/332 )
Functions    : 40% ( 4/10 )
Lines        : 50.01% ( 2300/4599 )
================================================================================
"""

TEXT_TABLE = """
 File      | % Stmts | % Branch | % Funcs | % Lines | Uncovered Line #s
-----------|---------|----------|---------|---------|-------------------
 All files |   72.50 |    61.25 |      80 |   73.10 |
 index.ts  |   72.50 |    61.25 |      80 |   73.10 | 4-9
"""

CLOVER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<coverage generated='1'>\n"
    "  <project timestamp='1'>\n"
    '    <metrics statements="200" coveredstatements="200" conditionals="40" '
    'coveredconditionals="40" methods="10" coveredmethods="10"/>\n'
    "  </project>\n"
    "</coverage>\n"
)


def _write(path: Path, text: str) -> Path:
    """Write ``text`` to ``path``, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class JsonSummaryTests(unittest.TestCase):
    """istanbul ``json-summary`` is the most direct signal."""

    def _summary(self, payload: object) -> Optional[assertion.CoverageSummary]:
        return assertion.summary_from_json_summary(_write(Path(self.tmp) / "s.json", json.dumps(payload)))

    def setUp(self) -> None:
        """Give each test its own scratch dir."""
        import tempfile

        self.tmp = tempfile.mkdtemp(prefix="qzp-jsts-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def test_reads_line_and_branch_percentages(self) -> None:
        """The happy path returns both numbers."""
        summary = self._summary(
            {"total": {"lines": {"pct": 100.0, "total": 10}, "branches": {"pct": 95.5, "total": 4}}}
        )
        assert summary is not None
        self.assertEqual(summary.lines_pct, 100.0)
        self.assertEqual(summary.branches_pct, 95.5)

    def test_zero_branch_total_reports_branches_as_not_applicable(self) -> None:
        """A file with no conditionals must not be scored 0% branches."""
        summary = self._summary({"total": {"lines": {"pct": 100.0, "total": 10}, "branches": {"pct": 0, "total": 0}}})
        assert summary is not None
        self.assertIsNone(summary.branches_pct)

    def test_zero_line_total_is_not_a_summary(self) -> None:
        """A report covering zero lines is UNMEASURED, not 100%."""
        self.assertIsNone(self._summary({"total": {"lines": {"pct": 100.0, "total": 0}}}))

    def test_missing_total_block_is_not_a_summary(self) -> None:
        """A structurally wrong file yields nothing rather than a false pass."""
        self.assertIsNone(self._summary({"nope": 1}))

    def test_non_mapping_document_is_not_a_summary(self) -> None:
        """A JSON array is not a json-summary report."""
        self.assertIsNone(self._summary([1, 2, 3]))

    def test_invalid_json_is_not_a_summary(self) -> None:
        """A truncated artifact must not crash the gate."""
        self.assertIsNone(assertion.summary_from_json_summary(_write(Path(self.tmp) / "b.json", "{oops")))

    def test_non_numeric_percentage_is_not_a_summary(self) -> None:
        """A string percentage is unparseable, so unmeasured."""
        self.assertIsNone(self._summary({"total": {"lines": {"pct": "high", "total": 10}}}))


class CloverTests(unittest.TestCase):
    """``clover.xml`` is vitest's default machine-readable reporter."""

    def setUp(self) -> None:
        """Give each test its own scratch dir."""
        import tempfile

        self.tmp = tempfile.mkdtemp(prefix="qzp-clover-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def test_reads_statements_and_conditionals(self) -> None:
        """Clover has no `lines` metric; statements is the documented proxy."""
        summary = assertion.summary_from_clover(_write(Path(self.tmp) / "clover.xml", CLOVER))
        assert summary is not None
        self.assertEqual(summary.lines_pct, 100.0)
        self.assertEqual(summary.branches_pct, 100.0)

    def test_partial_coverage_is_reported_as_a_percentage(self) -> None:
        """50/200 statements is 25%, not a pass."""
        text = CLOVER.replace('coveredstatements="200"', 'coveredstatements="50"')
        summary = assertion.summary_from_clover(_write(Path(self.tmp) / "clover.xml", text))
        assert summary is not None
        self.assertEqual(summary.lines_pct, 25.0)

    def test_zero_conditionals_is_not_applicable(self) -> None:
        """No conditionals means no branch score, not 0%."""
        text = CLOVER.replace('conditionals="40"', 'conditionals="0"').replace(
            'coveredconditionals="40"', 'coveredconditionals="0"'
        )
        summary = assertion.summary_from_clover(_write(Path(self.tmp) / "clover.xml", text))
        assert summary is not None
        self.assertIsNone(summary.branches_pct)

    def test_zero_statements_is_not_a_summary(self) -> None:
        """Zero statements measured is unmeasured."""
        text = CLOVER.replace('statements="200"', 'statements="0"')
        self.assertIsNone(assertion.summary_from_clover(_write(Path(self.tmp) / "clover.xml", text)))

    def test_missing_metrics_element_is_not_a_summary(self) -> None:
        """A clover file with no metrics block yields nothing."""
        self.assertIsNone(assertion.summary_from_clover(_write(Path(self.tmp) / "clover.xml", "<coverage/>")))

    def test_an_unreadable_path_is_not_a_summary(self) -> None:
        """An unreadable artifact must degrade to 'no evidence', not crash."""
        unreadable = Path(self.tmp) / "clover-dir"
        unreadable.mkdir()
        self.assertIsNone(assertion.summary_from_clover(unreadable))


class LcovTests(unittest.TestCase):
    """``lcov.info`` is what karma / Angular emit by default."""

    def setUp(self) -> None:
        """Give each test its own scratch dir."""
        import tempfile

        self.tmp = tempfile.mkdtemp(prefix="qzp-lcov-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def _lcov(self, body: str) -> Optional[assertion.CoverageSummary]:
        return assertion.summary_from_lcov(_write(Path(self.tmp) / "lcov.info", body))

    def test_sums_records_across_files(self) -> None:
        """LF/LH and BRF/BRH accumulate over every record."""
        summary = self._lcov("LF:10\nLH:5\nBRF:4\nBRH:1\nend_of_record\nLF:10\nLH:10\nBRF:4\nBRH:3\nend_of_record\n")
        assert summary is not None
        self.assertEqual(summary.lines_pct, 75.0)
        self.assertEqual(summary.branches_pct, 50.0)

    def test_zero_branches_found_is_not_applicable(self) -> None:
        """BRF:0 means no branches, not 0% branch coverage."""
        summary = self._lcov("LF:4\nLH:4\nBRF:0\nBRH:0\nend_of_record\n")
        assert summary is not None
        self.assertEqual(summary.lines_pct, 100.0)
        self.assertIsNone(summary.branches_pct)

    def test_no_line_records_is_not_a_summary(self) -> None:
        """An lcov file with no LF records measured nothing."""
        self.assertIsNone(self._lcov("TN:\nSF:/x.ts\nend_of_record\n"))

    def test_an_unreadable_path_is_not_a_summary(self) -> None:
        """An unreadable artifact must degrade to 'no evidence', not crash."""
        unreadable = Path(self.tmp) / "lcov-dir"
        unreadable.mkdir()
        self.assertIsNone(assertion.summary_from_lcov(unreadable))

    def test_malformed_counters_are_ignored(self) -> None:
        """A corrupt counter line must not crash or silently zero the total."""
        summary = self._lcov("LF:abc\nLF:10\nLH:10\nend_of_record\n")
        assert summary is not None
        self.assertEqual(summary.lines_pct, 100.0)


class TextSummaryTests(unittest.TestCase):
    """The last-resort fallback: the summary the run printed to stdout."""

    def test_parses_the_istanbul_text_summary_block(self) -> None:
        """momentstudio's karma output shape."""
        summary = assertion.summary_from_text(TEXT_SUMMARY)
        assert summary is not None
        self.assertEqual(summary.lines_pct, 50.01)
        self.assertEqual(summary.branches_pct, 30.12)

    def test_prefers_lines_but_falls_back_to_statements(self) -> None:
        """Some configurations print Statements without Lines."""
        summary = assertion.summary_from_text("Statements   : 88.5% ( 100/113 )\n")
        assert summary is not None
        self.assertEqual(summary.lines_pct, 88.5)
        self.assertIsNone(summary.branches_pct)

    def test_zero_denominator_is_not_a_summary(self) -> None:
        """`0/0` measured nothing."""
        self.assertIsNone(assertion.summary_from_text("Lines        : 100% ( 0/0 )\n"))

    def test_parses_the_all_files_table_row(self) -> None:
        """vitest / istanbul text-table output shape."""
        summary = assertion.summary_from_text(TEXT_TABLE)
        assert summary is not None
        self.assertEqual(summary.lines_pct, 73.10)
        self.assertEqual(summary.branches_pct, 61.25)

    def test_a_short_all_files_row_is_ignored(self) -> None:
        """A truncated table row must not be mistaken for a summary."""
        self.assertIsNone(assertion.summary_from_text("All files | 72.5 |\n"))

    def test_a_non_numeric_all_files_row_is_ignored(self) -> None:
        """A header-like row is not data."""
        self.assertIsNone(assertion.summary_from_text("All files | a | b | c | d |\n"))

    def test_unrelated_output_yields_nothing(self) -> None:
        """Build noise is not a coverage summary."""
        self.assertIsNone(assertion.summary_from_text("> vitest run\nnothing to see\n"))


class DiscoveryTests(unittest.TestCase):
    """Artifacts beat text; nested reporter directories are found."""

    def setUp(self) -> None:
        """Give each test its own scratch project dir."""
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="qzp-disc-"))
        self.addCleanup(__import__("shutil").rmtree, str(self.tmp), True)

    def test_prefers_the_json_summary_artifact_over_printed_text(self) -> None:
        """A rendering is weaker evidence than the data behind it."""
        _write(
            self.tmp / "coverage" / "coverage-summary.json",
            json.dumps({"total": {"lines": {"pct": 100.0, "total": 8}, "branches": {"pct": 100.0, "total": 2}}}),
        )
        summary = assertion.discover(self.tmp, TEXT_SUMMARY)
        assert summary is not None
        self.assertEqual(summary.lines_pct, 100.0)
        self.assertIn("coverage-summary.json", summary.origin)

    def test_finds_a_nested_karma_lcov_report(self) -> None:
        """Angular/karma writes coverage/<project>/lcov.info."""
        _write(self.tmp / "coverage" / "app" / "lcov.info", "LF:4\nLH:4\nend_of_record\n")
        summary = assertion.discover(self.tmp, "")
        assert summary is not None
        self.assertIn("lcov.info", summary.origin)

    def test_falls_back_to_clover_then_text(self) -> None:
        """Vitest's default reporter set still yields a number."""
        _write(self.tmp / "coverage" / "clover.xml", CLOVER)
        summary = assertion.discover(self.tmp, "")
        assert summary is not None
        self.assertIn("clover.xml", summary.origin)

    def test_text_is_used_when_no_artifact_exists(self) -> None:
        """No files on disk, but the run printed a summary."""
        summary = assertion.discover(self.tmp, TEXT_SUMMARY)
        assert summary is not None
        self.assertEqual(summary.origin, "the coverage run's printed summary")

    def test_nothing_at_all_yields_nothing(self) -> None:
        """THE anti-silent-pass property: no evidence means no summary."""
        self.assertIsNone(assertion.discover(self.tmp, "build ok\n"))

    def test_an_unparseable_artifact_does_not_shadow_a_usable_one(self) -> None:
        """A corrupt json-summary must not stop the lcov fallback."""
        _write(self.tmp / "coverage" / "coverage-summary.json", "{oops")
        _write(self.tmp / "coverage" / "lcov.info", "LF:4\nLH:4\nend_of_record\n")
        summary = assertion.discover(self.tmp, "")
        assert summary is not None
        self.assertIn("lcov.info", summary.origin)


class EvaluateTests(unittest.TestCase):
    """Threshold comparison, including the branch dimension."""

    def test_full_coverage_passes(self) -> None:
        """100/100 clears a 100 bar."""
        summary = assertion.CoverageSummary(origin="x", lines_pct=100.0, branches_pct=100.0)
        self.assertEqual(assertion.evaluate(summary, 100.0), [])

    def test_low_lines_fail(self) -> None:
        """momentstudio's 49.57% must not clear the bar."""
        summary = assertion.CoverageSummary(origin="x", lines_pct=49.57, branches_pct=100.0)
        findings = assertion.evaluate(summary, 100.0)
        self.assertEqual(len(findings), 1)
        self.assertIn("49.57", findings[0])

    def test_low_branches_fail(self) -> None:
        """The charter is 100% line AND branch."""
        summary = assertion.CoverageSummary(origin="x", lines_pct=100.0, branches_pct=30.12)
        findings = assertion.evaluate(summary, 100.0)
        self.assertEqual(len(findings), 1)
        self.assertIn("branch", findings[0])

    def test_not_applicable_branches_do_not_fail(self) -> None:
        """A project with no conditionals is not penalised."""
        summary = assertion.CoverageSummary(origin="x", lines_pct=100.0, branches_pct=None)
        self.assertEqual(assertion.evaluate(summary, 100.0), [])

    def test_both_dimensions_are_reported(self) -> None:
        """Findings accumulate so one run shows the whole gap."""
        summary = assertion.CoverageSummary(origin="x", lines_pct=10.0, branches_pct=20.0)
        self.assertEqual(len(assertion.evaluate(summary, 100.0)), 2)


class MainTests(unittest.TestCase):
    """CLI behaviour, including the momentstudio regression case."""

    def setUp(self) -> None:
        """Give each test its own scratch project dir."""
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="qzp-main-"))
        self.addCleanup(__import__("shutil").rmtree, str(self.tmp), True)

    def _run(self, *extra: str) -> Tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = assertion.main(["--project-dir", str(self.tmp), *extra])
        return code, out.getvalue() + err.getvalue()

    def test_momentstudio_shape_now_fails_instead_of_passing(self) -> None:
        """THE REGRESSION: this output used to print PASS gate-tests-coverage jsts.

        ``Lines`` is preferred over ``Statements`` when the runner prints both,
        so the reported figure is 50.01% rather than the 49.57% statements
        number quoted in the complaint. Either way it is nowhere near the bar.
        """
        log = _write(self.tmp / "run.log", TEXT_SUMMARY)
        code, output = self._run("--log", str(log))
        self.assertEqual(code, 1)
        self.assertIn("50.01", output)
        self.assertIn("30.12", output)
        self.assertIn("100.00", output)

    def test_full_coverage_passes(self) -> None:
        """A genuinely 100% project stays green."""
        _write(
            self.tmp / "coverage" / "coverage-summary.json",
            json.dumps({"total": {"lines": {"pct": 100.0, "total": 8}, "branches": {"pct": 100.0, "total": 2}}}),
        )
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertIn("PASS", output)

    def test_no_summary_at_all_fails_unconditionally(self) -> None:
        """The property no threshold can switch off: never pass unmeasured."""
        code, output = self._run("--min-percent", "1")
        self.assertEqual(code, 1)
        self.assertIn("unmeasured", output)

    def test_a_missing_log_path_is_tolerated(self) -> None:
        """A missing log is simply no text evidence, not a crash."""
        _write(self.tmp / "coverage" / "lcov.info", "LF:4\nLH:4\nend_of_record\n")
        code, _output = self._run("--log", str(self.tmp / "absent.log"))
        self.assertEqual(code, 0)

    def test_an_interim_threshold_is_honoured_and_printed(self) -> None:
        """A declared interim bar is allowed, but the real number is printed."""
        log = _write(self.tmp / "run.log", TEXT_SUMMARY)
        code, output = self._run("--log", str(log), "--min-percent", "30")
        self.assertEqual(code, 0)
        self.assertIn("50.01", output)
        self.assertIn("30", output)

    def test_a_zero_threshold_is_rejected_as_a_config_error(self) -> None:
        """A 0% bar would be a silent pass wearing a number."""
        code, output = self._run("--min-percent", "0")
        self.assertEqual(code, 2)
        self.assertIn("must be greater than 0", output)

    def test_an_over_hundred_threshold_is_rejected(self) -> None:
        """An unreachable bar is a defect, not a policy."""
        code, output = self._run("--min-percent", "101")
        self.assertEqual(code, 2)
        self.assertIn("must be greater than 0", output)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
