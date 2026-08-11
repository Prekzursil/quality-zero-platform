"""Tests for the gate-6 osv-scanner severity floor (T0 tiering).

The gate the owner complained about treated *every* ``osv-scanner`` exit 1 as a
hard block, regardless of severity, dev/prod scope, or whether a fix even
exists. That is how an UNSCORED Go stdlib advisory blocked a *security fix* for
17 days. ``scripts/quality/osv_severity_gate.py`` splits the same report into

* **BLOCK** - not dev-only AND ``max_severity`` parses to >= the floor AND a
  fixed version exists, and
* **T2** - everything else, which is still PRINTED (demoted, never hidden).

Both real-data fixtures under ``tests/fixtures/osv/`` were produced with the
pinned ``osv-scanner v2.3.8`` binary and trimmed to the fields the gate reads.
"""

from __future__ import absolute_import

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Tuple

from scripts.quality import osv_severity_gate as gate

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "osv"
MOMENTSTUDIO_FIXTURE = FIXTURES / "momentstudio_frontend_package_lock.osv.json"
DEVEXTREME_FIXTURE = FIXTURES / "devextreme_go_mod_1_25_11.osv.json"


def _package(**overrides: Any) -> Dict[str, Any]:
    """Build a minimal osv-scanner ``packages[]`` entry."""
    entry: Dict[str, Any] = {
        "package": {"name": "demo", "version": "1.0.0", "ecosystem": "npm"},
        "groups": [{"ids": ["GHSA-aaaa"], "max_severity": "9.1"}],
        "vulnerabilities": [
            {
                "id": "GHSA-aaaa",
                "affected": [{"ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "1.0.1"}]}]}],
            }
        ],
    }
    entry.update(overrides)
    return entry


def _document(*packages: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap ``packages`` in a one-source osv-scanner report."""
    return {"results": [{"source": {"path": "lock.json", "type": "lockfile"}, "packages": list(packages)}]}


class ParseSeverityTests(unittest.TestCase):
    """``parse_severity`` must be defensive: ``max_severity`` is a *string*."""

    def test_numeric_string_parses(self) -> None:
        """A CVSS score string becomes a float."""
        self.assertEqual(gate.parse_severity("8.8"), 8.8)

    def test_empty_string_is_unscored(self) -> None:
        """The measured Go-stdlib shape is ``""`` - unscored, not zero."""
        self.assertIsNone(gate.parse_severity(""))

    def test_none_is_unscored(self) -> None:
        """A missing key normalises to unscored rather than crashing."""
        self.assertIsNone(gate.parse_severity(None))

    def test_garbage_is_unscored(self) -> None:
        """Non-numeric text is unscored, never silently 0.0."""
        self.assertIsNone(gate.parse_severity("HIGH"))

    def test_real_number_passes_through(self) -> None:
        """A already-numeric value is accepted."""
        self.assertEqual(gate.parse_severity(7), 7.0)

    def test_list_is_unscored(self) -> None:
        """A structurally wrong value is unscored, not a TypeError."""
        self.assertIsNone(gate.parse_severity(["8.8"]))


class NormalizeGroupsTests(unittest.TestCase):
    """``dependency_groups`` is ABSENT (not ``[]``) for production deps."""

    def test_absent_is_empty(self) -> None:
        """``None`` (key absent) normalises to an empty tuple."""
        self.assertEqual(gate.normalize_groups(None), ())

    def test_lowercased_and_stripped(self) -> None:
        """Group names are compared case-insensitively."""
        self.assertEqual(gate.normalize_groups([" Dev ", "OPTIONAL"]), ("dev", "optional"))

    def test_non_list_is_empty(self) -> None:
        """A scalar value is treated as no groups at all."""
        self.assertEqual(gate.normalize_groups("dev"), ())

    def test_non_string_entries_are_coerced(self) -> None:
        """A stray non-string entry does not crash normalisation."""
        self.assertEqual(gate.normalize_groups(["dev", 3]), ("dev", "3"))


class IsDevOnlyTests(unittest.TestCase):
    """Only a *development* dependency may be demoted for scope."""

    def test_production_dependency_is_not_dev_only(self) -> None:
        """Absent groups == production == never demoted for scope."""
        self.assertFalse(gate.is_dev_only(()))

    def test_dev_is_dev_only(self) -> None:
        """The plain ``["dev"]`` shape is dev-only."""
        self.assertTrue(gate.is_dev_only(("dev",)))

    def test_dev_plus_optional_is_dev_only(self) -> None:
        """``["dev", "optional"]`` (an optional dep OF a dev dep) is dev-only."""
        self.assertTrue(gate.is_dev_only(("dev", "optional")))

    def test_optional_alone_is_not_dev_only(self) -> None:
        """npm ``optionalDependencies`` ARE installed in production."""
        self.assertFalse(gate.is_dev_only(("optional",)))

    def test_unknown_group_is_not_dev_only(self) -> None:
        """An unrecognised group never buys a demotion."""
        self.assertFalse(gate.is_dev_only(("dev", "peer")))


class FixedVersionsTests(unittest.TestCase):
    """A finding is only actionable when the report names a fixed version."""

    def test_collects_fixed_events_for_matching_ids(self) -> None:
        """Fixed events are gathered across ranges and sorted."""
        self.assertEqual(gate.fixed_versions_for(_package(), ["GHSA-aaaa"]), ("1.0.1",))

    def test_ignores_non_matching_ids(self) -> None:
        """A group whose ids are absent from ``vulnerabilities`` yields no fix."""
        self.assertEqual(gate.fixed_versions_for(_package(), ["GHSA-zzzz"]), ())

    def test_tolerates_missing_vulnerability_structures(self) -> None:
        """Missing ``affected`` / ``ranges`` / ``events`` never raises."""
        entry = _package(vulnerabilities=[{"id": "GHSA-aaaa"}, {"id": "GHSA-aaaa", "affected": [{}]}])
        self.assertEqual(gate.fixed_versions_for(entry, ["GHSA-aaaa"]), ())

    def test_ignores_events_without_a_fixed_key(self) -> None:
        """``introduced``-only ranges mean 'no fix published'."""
        entry = _package(
            vulnerabilities=[
                {"id": "GHSA-aaaa", "affected": [{"ranges": [{"events": [{"introduced": "0"}]}]}]},
            ]
        )
        self.assertEqual(gate.fixed_versions_for(entry, ["GHSA-aaaa"]), ())

    def test_tolerates_non_mapping_members(self) -> None:
        """Stray non-mapping list members are skipped, not fatal."""
        entry = _package(vulnerabilities=["nope", {"id": "GHSA-aaaa", "affected": ["nope", {"ranges": ["nope"]}]}])
        self.assertEqual(gate.fixed_versions_for(entry, ["GHSA-aaaa"]), ())

    def test_tolerates_non_mapping_events(self) -> None:
        """A non-mapping event entry is skipped."""
        entry = _package(
            vulnerabilities=[{"id": "GHSA-aaaa", "affected": [{"ranges": [{"events": ["nope"]}]}]}],
        )
        self.assertEqual(gate.fixed_versions_for(entry, ["GHSA-aaaa"]), ())


class IterFindingsTests(unittest.TestCase):
    """Every finding-GROUP becomes exactly one ``Finding``."""

    def test_empty_document_yields_nothing(self) -> None:
        """A totally empty ``results[]`` is a clean tree, not an error."""
        self.assertEqual(gate.iter_findings({"results": []}), [])

    def test_missing_results_key_yields_nothing(self) -> None:
        """A report without ``results`` at all is also clean."""
        self.assertEqual(gate.iter_findings({}), [])

    def test_non_mapping_document_yields_nothing(self) -> None:
        """A JSON array at top level is not a report."""
        self.assertEqual(gate.iter_findings([]), [])

    def test_non_mapping_members_are_skipped(self) -> None:
        """Stray non-mapping results/packages/groups members are skipped."""
        document = {"results": ["nope", {"packages": ["nope", {"groups": ["nope"]}]}]}
        self.assertEqual(gate.iter_findings(document), [])

    def test_populates_every_field(self) -> None:
        """A well-formed group is fully mapped onto ``Finding``."""
        findings = gate.iter_findings(_document(_package(dependency_groups=["dev"])))
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.source, "lock.json")
        self.assertEqual(finding.package, "demo")
        self.assertEqual(finding.version, "1.0.0")
        self.assertEqual(finding.ecosystem, "npm")
        self.assertEqual(finding.dependency_groups, ("dev",))
        self.assertEqual(finding.ids, ("GHSA-aaaa",))
        self.assertEqual(finding.max_severity_raw, "9.1")
        self.assertEqual(finding.severity, 9.1)
        self.assertEqual(finding.fixed_versions, ("1.0.1",))
        self.assertTrue(finding.dev_only)

    def test_missing_package_metadata_defaults(self) -> None:
        """A group with no ``package`` block still produces a printable row."""
        document = {"results": [{"packages": [{"groups": [{}]}]}]}
        finding = gate.iter_findings(document)[0]
        self.assertEqual(finding.source, "<unknown>")
        self.assertEqual(finding.package, "<unknown>")
        self.assertEqual(finding.version, "<unknown>")
        self.assertEqual(finding.ecosystem, "<unknown>")
        self.assertEqual(finding.ids, ())
        self.assertIsNone(finding.severity)

    def test_non_mapping_source_and_package_blocks(self) -> None:
        """``source`` / ``package`` present but not a mapping are tolerated."""
        document = {"results": [{"source": "nope", "packages": [{"package": "nope", "groups": [{}]}]}]}
        finding = gate.iter_findings(document)[0]
        self.assertEqual(finding.source, "<unknown>")
        self.assertEqual(finding.package, "<unknown>")


class ClassifyTests(unittest.TestCase):
    """The BLOCK predicate is a three-way AND; anything else is T2."""

    def _classify_one(self, **overrides: Any):
        blocking, demoted = gate.classify(gate.iter_findings(_document(_package(**overrides))), gate.SEVERITY_FLOOR)
        return blocking, demoted

    def test_high_severity_production_with_fix_blocks(self) -> None:
        """The canonical blocking shape."""
        blocking, demoted = self._classify_one()
        self.assertEqual(len(blocking), 1)
        self.assertEqual(demoted, [])

    def test_dev_only_is_demoted(self) -> None:
        """A dev-only dependency never reds a product branch."""
        blocking, demoted = self._classify_one(dependency_groups=["dev"])
        self.assertEqual(blocking, [])
        self.assertIn("dev-only", demoted[0].reasons)

    def test_unscored_is_demoted(self) -> None:
        """The Go-stdlib ``max_severity: ""`` shape is demoted, not blocking."""
        blocking, demoted = self._classify_one(groups=[{"ids": ["GHSA-aaaa"], "max_severity": ""}])
        self.assertEqual(blocking, [])
        self.assertIn("unscored", demoted[0].reasons)

    def test_below_floor_is_demoted(self) -> None:
        """A Medium/Low CVSS score is inventory, not a blocker."""
        blocking, demoted = self._classify_one(groups=[{"ids": ["GHSA-aaaa"], "max_severity": "5.1"}])
        self.assertEqual(blocking, [])
        self.assertIn("below-floor", demoted[0].reasons)

    def test_unfixable_is_demoted(self) -> None:
        """No fix at any price means the owner cannot drive the gate green."""
        blocking, demoted = self._classify_one(vulnerabilities=[{"id": "GHSA-aaaa", "affected": []}])
        self.assertEqual(blocking, [])
        self.assertIn("no-fix-available", demoted[0].reasons)

    def test_reasons_accumulate(self) -> None:
        """Several demotion reasons are all reported, not just the first."""
        _blocking, demoted = self._classify_one(
            dependency_groups=["dev"],
            groups=[{"ids": ["GHSA-aaaa"], "max_severity": ""}],
            vulnerabilities=[],
        )
        self.assertEqual(demoted[0].reasons, ("dev-only", "unscored", "no-fix-available"))

    def test_floor_is_inclusive(self) -> None:
        """A score exactly on the floor blocks."""
        blocking, _demoted = self._classify_one(groups=[{"ids": ["GHSA-aaaa"], "max_severity": "7.0"}])
        self.assertEqual(len(blocking), 1)

    def test_custom_floor_is_honoured(self) -> None:
        """``--min-severity`` moves the boundary."""
        findings = gate.iter_findings(_document(_package(groups=[{"ids": ["GHSA-aaaa"], "max_severity": "5.1"}])))
        blocking, _demoted = gate.classify(findings, 5.0)
        self.assertEqual(len(blocking), 1)


class RenderReportTests(unittest.TestCase):
    """Both sets are always printed - a demoted finding is never hidden."""

    def test_clean_report(self) -> None:
        """Zero findings still prints an explicit zero."""
        text = gate.render_report([], [], gate.SEVERITY_FLOOR)
        self.assertIn("BLOCKING (0)", text)
        self.assertIn("T2 INVENTORY (0)", text)
        self.assertIn("none", text)

    def test_blocking_rows_are_rendered(self) -> None:
        """A blocking row names the score, the advisory and the fix."""
        blocking, demoted = gate.classify(gate.iter_findings(_document(_package())), gate.SEVERITY_FLOOR)
        text = gate.render_report(blocking, demoted, gate.SEVERITY_FLOOR)
        self.assertIn("BLOCKING (1)", text)
        self.assertIn("GHSA-aaaa", text)
        self.assertIn("fixed in 1.0.1", text)
        self.assertIn("demo 1.0.0", text)

    def test_demoted_rows_carry_their_reasons(self) -> None:
        """A T2 row states WHY it was demoted and still shows the data."""
        package = _package(
            dependency_groups=["dev"],
            groups=[{"ids": ["GHSA-aaaa"], "max_severity": ""}],
            vulnerabilities=[],
        )
        blocking, demoted = gate.classify(gate.iter_findings(_document(package)), gate.SEVERITY_FLOOR)
        text = gate.render_report(blocking, demoted, gate.SEVERITY_FLOOR)
        self.assertIn("T2 INVENTORY (1)", text)
        self.assertIn("dev-only+unscored", text)
        self.assertIn("severity -", text)
        self.assertIn("no published fix", text)


class LoadDocumentTests(unittest.TestCase):
    """The report arrives either on stdin or as a path."""

    def test_reads_a_path(self) -> None:
        """A path argument is read from disk."""
        document = gate.load_document(str(DEVEXTREME_FIXTURE))
        self.assertIn("results", document)

    def test_reads_stdin(self) -> None:
        """``-`` reads the report from stdin."""
        stream = io.StringIO(json.dumps({"results": []}))
        self.assertEqual(gate.load_document("-", stdin=stream), {"results": []})

    def test_missing_path_raises(self) -> None:
        """A missing file is an input error, not an empty (clean) report."""
        with self.assertRaises(gate.ReportInputError):
            gate.load_document(str(FIXTURES / "does-not-exist.json"))

    def test_invalid_json_raises(self) -> None:
        """Unparseable input is an input error, never a silent pass."""
        with self.assertRaises(gate.ReportInputError):
            gate.load_document("-", stdin=io.StringIO("{not json"))


class MainTests(unittest.TestCase):
    """End-to-end CLI behaviour, including both real-data fixtures."""

    def _run(self, argv: List[str]) -> Tuple[int, str]:
        buffer = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(buffer), redirect_stderr(errors):
            code = gate.main(argv)
        return code, buffer.getvalue() + errors.getvalue()

    def test_momentstudio_fixture_yields_exactly_four_blocking(self) -> None:
        """Real data: 29 finding-groups from the frontend lockfile -> 4 block."""
        code, output = self._run([str(MOMENTSTUDIO_FIXTURE)])
        self.assertEqual(code, gate.EXIT_BLOCKED)
        self.assertIn("BLOCKING (4)", output)
        self.assertIn("T2 INVENTORY (25)", output)
        for name in ("@angular/common", "@angular/platform-server", "@angular/compiler", "@angular/core"):
            self.assertIn(name, output)
        # The dev-only lint-toolchain packages that red the fleet today are
        # demoted, not hidden.
        self.assertIn("brace-expansion", output)

    def test_devextreme_go_fixture_yields_zero_blocking(self) -> None:
        """Real data: two UNSCORED Go stdlib advisories must not block."""
        code, output = self._run([str(DEVEXTREME_FIXTURE)])
        self.assertEqual(code, gate.EXIT_OK)
        self.assertIn("BLOCKING (0)", output)
        self.assertIn("T2 INVENTORY (2)", output)
        self.assertIn("GO-2026-4970", output)
        self.assertIn("GO-2026-5856", output)

    def test_empty_report_passes(self) -> None:
        """An empty ``results[]`` exits 0."""
        code, output = self._run(["-"])
        self.assertEqual(code, gate.EXIT_OK)
        self.assertIn("BLOCKING (0)", output)

    def test_min_severity_override(self) -> None:
        """Lowering the floor promotes previously-demoted findings."""
        code, output = self._run([str(DEVEXTREME_FIXTURE), "--min-severity", "0"])
        # Both Go advisories stay unscored, so the floor cannot promote them.
        self.assertEqual(code, gate.EXIT_OK)
        self.assertIn("unscored", output)

    def test_input_error_exits_two(self) -> None:
        """A broken report is exit 2 - distinct from 'clean' and from 'blocked'."""
        code, _output = self._run([str(FIXTURES / "does-not-exist.json")])
        self.assertEqual(code, gate.EXIT_INPUT_ERROR)

    def setUp(self) -> None:
        """Feed an empty report on stdin for the argv-less/dash cases."""
        self._stdin_backup = gate.sys.stdin
        gate.sys.stdin = io.StringIO(json.dumps({"results": []}))
        self.addCleanup(self._restore_stdin)

    def _restore_stdin(self) -> None:
        """Put the real stdin back."""
        gate.sys.stdin = self._stdin_backup


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
