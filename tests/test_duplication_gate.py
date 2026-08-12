"""Tests for the gate-8 duplication floor (T1 new-code-only tiering).

``scripts/quality/duplication_gate.py`` reads a ``jscpd --reporters json`` report
plus the change's unified diff and splits the detected clone pairs into

* **BLOCK** - at least one side of the pair sits on lines this change added or
  modified, i.e. the change introduced a copy, and
* **T2** - clone pairs entirely on untouched lines, which are still PRINTED
  (demoted, never hidden).

"At least one side" is the rule, and the recorded fixture is exactly why: the
clone pair is ``src/legacy_settings.py`` (untouched) against
``src/new_settings.py`` (a brand-new file that copy-pasted it). Requiring BOTH
sides to be new would let every copy-paste-from-legacy through - the single most
common way duplication actually enters a codebase.

Both jscpd fixtures are real ``jscpd@4.0.5 --reporters json`` output.
"""

from __future__ import absolute_import

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Tuple

from tests.newcode_scope_support import NewCodeScopeContract

from scripts.quality import duplication_gate as gate

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "newcode"
MIXED_JSON = FIXTURES / "jscpd_mixed.json"
CLEAN_JSON = FIXTURES / "jscpd_clean.json"
NEWCODE_DIFF = FIXTURES / "newcode.diff"


def _run(argv: List[str]) -> Tuple[int, str, str]:
    """Invoke ``main`` and capture the exit code with both streams, kept APART.

    The passing path asserts that stderr is *empty*, which the concatenating
    ``_run`` in ``test_osv_severity_gate.py`` cannot express.
    """
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = gate.main(argv)
    return code, out.getvalue(), err.getvalue()


def _side(name: str, start: int, end: int) -> Dict[str, Any]:
    """Build one side of a synthetic clone pair."""
    return {"name": name, "start": start, "end": end}


def _document(duplicates: List[Dict[str, Any]], clones: Any = None) -> str:
    """Serialise a synthetic jscpd document, mirroring the real shape."""
    total = {"clones": len(duplicates) if clones is None else clones}
    return json.dumps({"duplicates": duplicates, "statistics": {"total": total}})


def _pair(first: str, second: str, start: int = 6, end: int = 27) -> Dict[str, Any]:
    """Build a synthetic clone pair between two files."""
    return {
        "firstFile": _side(first, start, end),
        "secondFile": _side(second, start, end),
        "lines": end - start + 1,
        "tokens": 116,
        "format": "python",
    }


class DuplicationScopeContractTests(NewCodeScopeContract, unittest.TestCase):
    """Run the shared new-code scoping contract against THIS module's copy."""

    module = gate


class ParseJscpdJsonTests(unittest.TestCase):
    """The parser must read real jscpd output, including its path convention."""

    def test_real_report_yields_the_recorded_clone_pair(self) -> None:
        """One python clone pair, legacy against the new copy."""
        report = gate.parse_jscpd_json(MIXED_JSON.read_text(encoding="utf-8"))
        self.assertEqual(len(report.clones), 1)
        clone = report.clones[0]
        self.assertEqual(clone.first.path, "src/legacy_settings.py")
        self.assertEqual(clone.second.path, "src/new_settings.py")

    def test_the_recorded_spans_are_read_verbatim(self) -> None:
        """Line-level scoping is only possible because jscpd reports both spans."""
        clone = gate.parse_jscpd_json(MIXED_JSON.read_text(encoding="utf-8")).clones[0]
        self.assertEqual((clone.first.start, clone.first.end), (6, 27))
        self.assertEqual((clone.second.start, clone.second.end), (6, 27))

    def test_the_size_and_language_are_carried_for_reporting(self) -> None:
        """A T2 row must be actionable later, so it records how big the clone is."""
        clone = gate.parse_jscpd_json(MIXED_JSON.read_text(encoding="utf-8")).clones[0]
        self.assertEqual(clone.lines, 22)
        self.assertEqual(clone.tokens, 116)
        self.assertEqual(clone.language, "python")

    def test_windows_separators_in_jscpd_names_are_normalized(self) -> None:
        """jscpd recorded ``src\\legacy_settings.py``; the diff says ``src/...``."""
        raw = json.loads(MIXED_JSON.read_text(encoding="utf-8"))
        self.assertIn("\\", raw["duplicates"][0]["firstFile"]["name"])
        report = gate.parse_jscpd_json(MIXED_JSON.read_text(encoding="utf-8"))
        self.assertEqual(report.clones[0].first.path, "src/legacy_settings.py")

    def test_absolute_names_are_relativized_against_the_root(self) -> None:
        """``jscpd --absolute`` emits absolute paths that would never match a diff key."""
        document = _document([_pair("/w/repo/src/a.py", "/w/repo/src/b.py")])
        report = gate.parse_jscpd_json(document, root="/w/repo")
        self.assertEqual(report.clones[0].first.path, "src/a.py")
        self.assertEqual(report.clones[0].second.path, "src/b.py")

    def test_the_clean_report_yields_no_clones(self) -> None:
        """The PASS side of the both-states pair."""
        report = gate.parse_jscpd_json(CLEAN_JSON.read_text(encoding="utf-8"))
        self.assertEqual(report.clones, [])
        self.assertEqual(report.skipped_entries, 0)

    def test_the_reported_total_is_captured_for_cross_checking(self) -> None:
        """jscpd's own count is an independent signal against a truncated report."""
        self.assertEqual(gate.parse_jscpd_json(MIXED_JSON.read_text(encoding="utf-8")).reported_clones, 1)
        self.assertEqual(gate.parse_jscpd_json(CLEAN_JSON.read_text(encoding="utf-8")).reported_clones, 0)

    def test_a_report_without_statistics_has_no_reported_total(self) -> None:
        """The cross-check is optional; its absence must not crash the gate."""
        report = gate.parse_jscpd_json(json.dumps({"duplicates": []}))
        self.assertIsNone(report.reported_clones)

    def test_a_non_numeric_reported_total_is_ignored(self) -> None:
        """A malformed statistics block must not be read as a real count."""
        document = json.dumps({"duplicates": [], "statistics": {"total": {"clones": "many"}}})
        self.assertIsNone(gate.parse_jscpd_json(document).reported_clones)

    def test_a_statistics_block_of_the_wrong_type_is_ignored(self) -> None:
        """Defensive: ``statistics`` or ``total`` may not be objects."""
        self.assertIsNone(gate.parse_jscpd_json(json.dumps({"duplicates": [], "statistics": []})).reported_clones)
        self.assertIsNone(
            gate.parse_jscpd_json(json.dumps({"duplicates": [], "statistics": {"total": 5}})).reported_clones
        )

    def test_invalid_json_is_rejected(self) -> None:
        """A truncated report is not an empty one."""
        with self.assertRaises(gate.ReportError):
            gate.parse_jscpd_json("{not json")

    def test_a_non_object_document_is_rejected(self) -> None:
        """A JSON array at the top level is not a jscpd report."""
        with self.assertRaises(gate.ReportError):
            gate.parse_jscpd_json("[]")

    def test_a_missing_duplicates_key_is_rejected(self) -> None:
        """No ``duplicates`` key means nothing was measured, which is not clean."""
        with self.assertRaises(gate.ReportError):
            gate.parse_jscpd_json(json.dumps({"statistics": {}}))

    def test_a_non_list_duplicates_value_is_rejected(self) -> None:
        """The shape has to be the shape, or the count means nothing."""
        with self.assertRaises(gate.ReportError):
            gate.parse_jscpd_json(json.dumps({"duplicates": {}}))

    def test_an_entry_missing_a_side_is_counted_as_skipped(self) -> None:
        """A pair we cannot locate must be disclosed, never silently dropped."""
        report = gate.parse_jscpd_json(json.dumps({"duplicates": [{"firstFile": _side("src/a.py", 1, 5)}]}))
        self.assertEqual(report.clones, [])
        self.assertEqual(report.skipped_entries, 1)

    def test_an_entry_with_a_non_numeric_span_is_counted_as_skipped(self) -> None:
        """Spans are load-bearing for scoping; a bad one cannot be guessed."""
        broken = {"firstFile": _side("src/a.py", "x", 5), "secondFile": _side("src/b.py", 1, 5)}
        report = gate.parse_jscpd_json(json.dumps({"duplicates": [broken]}))
        self.assertEqual(report.skipped_entries, 1)

    def test_an_entry_missing_a_name_is_counted_as_skipped(self) -> None:
        """Without a path there is nothing to compare against the diff."""
        broken = {"firstFile": {"start": 1, "end": 5}, "secondFile": _side("src/b.py", 1, 5)}
        report = gate.parse_jscpd_json(json.dumps({"duplicates": [broken]}))
        self.assertEqual(report.skipped_entries, 1)

    def test_an_entry_that_is_not_an_object_is_counted_as_skipped(self) -> None:
        """Defensive: the list may hold anything."""
        report = gate.parse_jscpd_json(json.dumps({"duplicates": ["nope"]}))
        self.assertEqual(report.skipped_entries, 1)

    def test_optional_reporting_fields_default_instead_of_skipping(self) -> None:
        """``lines``/``tokens``/``format`` are report-only; their absence is tolerable."""
        minimal = {"firstFile": _side("src/a.py", 1, 5), "secondFile": _side("src/b.py", 1, 5)}
        report = gate.parse_jscpd_json(json.dumps({"duplicates": [minimal]}))
        self.assertEqual(report.skipped_entries, 0)
        self.assertEqual((report.clones[0].lines, report.clones[0].tokens), (0, 0))
        self.assertEqual(report.clones[0].language, "unknown")

    def test_a_non_numeric_optional_field_falls_back_to_the_default(self) -> None:
        """A junk ``lines`` value must not take down a locatable clone."""
        entry = {"firstFile": _side("src/a.py", 1, 5), "secondFile": _side("src/b.py", 1, 5), "lines": "lots"}
        report = gate.parse_jscpd_json(json.dumps({"duplicates": [entry]}))
        self.assertEqual(report.clones[0].lines, 0)


class ClassifyTests(unittest.TestCase):
    """The tiering decision, driven by the real recorded report."""

    def setUp(self) -> None:
        """Load the recorded report and diff once per test."""
        self.report = gate.parse_jscpd_json(MIXED_JSON.read_text(encoding="utf-8"))
        self.ranges = gate.parse_added_ranges(NEWCODE_DIFF.read_text(encoding="utf-8"))

    def test_a_copy_paste_into_a_new_file_blocks(self) -> None:
        """THE case: only the SECOND side is new, and that is enough to block."""
        verdict = gate.classify(self.report.clones, self.ranges, scoped=True)
        self.assertEqual(len(verdict.blocking), 1)
        self.assertEqual(verdict.inventory, [])

    def test_a_clone_touched_only_on_the_first_side_also_blocks(self) -> None:
        """The rule must be symmetric, or side order would decide the verdict."""
        verdict = gate.classify(self.report.clones, {"src/legacy_settings.py": [(6, 10)]}, scoped=True)
        self.assertEqual(len(verdict.blocking), 1)

    def test_a_clone_on_untouched_lines_is_demoted(self) -> None:
        """Pre-existing duplication must never block; that is the treadmill."""
        verdict = gate.classify(self.report.clones, {"src/new_settings.py": [(100, 120)]}, scoped=True)
        self.assertEqual(verdict.blocking, [])
        self.assertEqual(len(verdict.inventory), 1)

    def test_unscoped_mode_demotes_everything(self) -> None:
        """A push to main has no diff base, so nothing may block."""
        verdict = gate.classify(self.report.clones, {}, scoped=False)
        self.assertEqual(verdict.blocking, [])
        self.assertEqual(len(verdict.inventory), 1)

    def test_a_clean_report_produces_an_empty_verdict(self) -> None:
        """The PASS side: no clone pairs at all."""
        clean = gate.parse_jscpd_json(CLEAN_JSON.read_text(encoding="utf-8"))
        verdict = gate.classify(clean.clones, self.ranges, scoped=True)
        self.assertEqual(verdict.blocking, [])
        self.assertEqual(verdict.inventory, [])


class RenderTests(unittest.TestCase):
    """Demoted is not hidden: the T2 set must be printed in full."""

    def setUp(self) -> None:
        """Load the recorded report and diff once per test."""
        self.report = gate.parse_jscpd_json(MIXED_JSON.read_text(encoding="utf-8"))
        self.ranges = gate.parse_added_ranges(NEWCODE_DIFF.read_text(encoding="utf-8"))

    def test_a_blocking_row_names_both_sides_with_their_spans(self) -> None:
        """The author has to be able to find both copies from the log alone."""
        verdict = gate.classify(self.report.clones, self.ranges, scoped=True)
        text = gate.render_report(self.report, verdict)
        self.assertIn("src/new_settings.py:6-27", text)
        self.assertIn("src/legacy_settings.py:6-27", text)
        self.assertIn("22 line", text)

    def test_a_demoted_row_is_printed_too(self) -> None:
        """T2 visible on the green path, or the gate hides what it did not enforce."""
        verdict = gate.classify(self.report.clones, {}, scoped=False)
        text = gate.render_report(self.report, verdict)
        self.assertIn("T2 INVENTORY (1)", text)
        self.assertIn("src/legacy_settings.py:6-27", text)

    def test_an_empty_report_says_so_explicitly(self) -> None:
        """Silence is indistinguishable from a gate that never ran."""
        clean = gate.parse_jscpd_json(CLEAN_JSON.read_text(encoding="utf-8"))
        verdict = gate.classify(clean.clones, self.ranges, scoped=True)
        self.assertIn("0 clone pair(s)", gate.render_report(clean, verdict))

    def test_unscoped_mode_is_labelled_as_inventory_only(self) -> None:
        """A reader must be able to tell a T2-only run from a scoped one."""
        verdict = gate.classify(self.report.clones, {}, scoped=False)
        self.assertIn("inventory only", gate.render_report(self.report, verdict))

    def test_scoped_mode_is_labelled(self) -> None:
        """The scope line must state which definition of new code was applied."""
        verdict = gate.classify(self.report.clones, self.ranges, scoped=True)
        self.assertIn("T1 new-code-only", gate.render_report(self.report, verdict))

    def test_skipped_entries_are_disclosed(self) -> None:
        """A partially-unreadable report must not read as a clean one."""
        report = gate.parse_jscpd_json(json.dumps({"duplicates": ["nope"]}))
        verdict = gate.classify(report.clones, {}, scoped=False)
        self.assertIn("1 unreadable entr", gate.render_report(report, verdict))

    def test_a_count_mismatch_against_jscpd_own_total_is_disclosed(self) -> None:
        """Two independent signals disagreeing means the report is not trustworthy."""
        report = gate.parse_jscpd_json(_document([_pair("src/a.py", "src/b.py")], clones=5))
        verdict = gate.classify(report.clones, {}, scoped=False)
        self.assertIn("jscpd reported 5", gate.render_report(report, verdict))

    def test_a_matching_count_is_not_flagged(self) -> None:
        """The disclosure must be conditional, or it is noise on every run."""
        verdict = gate.classify(self.report.clones, {}, scoped=False)
        self.assertNotIn("jscpd reported", gate.render_report(self.report, verdict))

    def test_a_report_without_a_total_is_not_flagged(self) -> None:
        """No cross-check available is not the same as a failed cross-check."""
        report = gate.parse_jscpd_json(json.dumps({"duplicates": []}))
        verdict = gate.classify(report.clones, {}, scoped=False)
        self.assertNotIn("jscpd reported", gate.render_report(report, verdict))


class MainTests(unittest.TestCase):
    """End-to-end: the four both-states directions, through the real CLI."""

    def test_a_copy_pasted_block_exits_blocked(self) -> None:
        """BOTH-STATES 3/4: a new copy-paste FAILS the lane."""
        code, out, err = _run(["--json", str(MIXED_JSON), "--diff", str(NEWCODE_DIFF)])
        self.assertEqual(code, gate.EXIT_BLOCKED)
        self.assertIn("FAIL gate-duplication", err)
        self.assertIn("src/new_settings.py:6-27", out)

    def test_unique_code_exits_ok(self) -> None:
        """BOTH-STATES 4/4: a report with no clones PASSES the lane."""
        code, out, err = _run(["--json", str(CLEAN_JSON), "--diff", str(NEWCODE_DIFF)])
        self.assertEqual(code, gate.EXIT_OK)
        self.assertIn("PASS gate-duplication", out)
        self.assertEqual(err, "")

    def test_without_a_diff_the_gate_never_blocks(self) -> None:
        """A push to main has no base; it emits inventory and passes."""
        code, out, err = _run(["--json", str(MIXED_JSON)])
        self.assertEqual(code, gate.EXIT_OK)
        self.assertIn("inventory only", out)
        self.assertIn("src/legacy_settings.py:6-27", out)
        self.assertEqual(err, "")

    def test_a_missing_report_is_a_config_error(self) -> None:
        """An unmeasured tree must never be reported as a clean one."""
        code, _, err = _run(["--json", str(FIXTURES / "does_not_exist.json")])
        self.assertEqual(code, gate.EXIT_CONFIG_ERROR)
        self.assertIn("ERROR gate-duplication", err)

    def test_a_malformed_report_is_a_config_error(self) -> None:
        """Exit 2 is distinguishable from both 0 and 1 by the workflow."""
        broken = FIXTURES / "does_not_exist_broken.json"
        self.addCleanup(broken.unlink, missing_ok=True)
        broken.write_text("{not json", encoding="utf-8")
        code, _, err = _run(["--json", str(broken)])
        self.assertEqual(code, gate.EXIT_CONFIG_ERROR)
        self.assertIn("ERROR gate-duplication", err)

    def test_a_missing_diff_file_is_a_config_error(self) -> None:
        """If the workflow promised a diff, an unreadable one is not "no scope"."""
        code, _, err = _run(["--json", str(MIXED_JSON), "--diff", str(FIXTURES / "nope.diff")])
        self.assertEqual(code, gate.EXIT_CONFIG_ERROR)
        self.assertIn("ERROR gate-duplication", err)

    def test_the_root_option_is_accepted(self) -> None:
        """CI can invoke jscpd with --absolute; the gate must cope."""
        code, out, _ = _run(["--json", str(MIXED_JSON), "--root", str(ROOT)])
        self.assertEqual(code, gate.EXIT_OK)
        self.assertIn("src/legacy_settings.py", out)


if __name__ == "__main__":
    unittest.main()
