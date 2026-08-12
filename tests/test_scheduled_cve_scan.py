"""Tests for the scheduled fleet-wide dependency-CVE sweep.

``scripts/quality/scheduled_cve_scan.py`` closes a STRUCTURAL gap, not a code
defect. A dependency advisory is not a function of the code: it is time-varying.
A PR-diff gate cannot fire without a diff, so a NEW advisory published against
UNCHANGED code stays invisible until somebody happens to touch that repo. PR
#286 correctly demoted dev-only / unscored / unfixable findings out of the
blocking tier - but nothing surfaced the demoted tier, so those findings went
nowhere at all.

The tests below pin four things the sweep must never get wrong:

1. **The osv-scanner exit-code contract is gate 6's, verbatim.** ``0`` clean,
   ``1`` findings, ``128`` nothing to scan, ``127``/``129``/``130`` a SCAN
   ERROR that is *not* a vulnerability verdict. A scan error must never be
   reported as a clean result, and must never close a tracking issue.
2. **One issue per repo, updated in place.** An issue-per-run is a notification
   spammer that gets muted within a week, which defeats the entire purpose.
3. **T0 / T2 are separated** so a reader can tell "act now" from "for
   information", reusing the same classifier the gate uses so T0 is a faithful
   "would block" prediction.
4. **It is observability, not a gate** - its check context appears in no
   ruleset's ``required_status_checks``.

BOTH-STATES coverage is explicit throughout: every predicate is fed a
known-BAD input and asserted non-clean, and a known-GOOD input and asserted
clean. A gate never seen red is indistinguishable from a no-op.
"""

from __future__ import absolute_import

import datetime as dt
import io
import json
import subprocess  # nosec B404 — CompletedProcess is used to build fake runner results
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml  # type: ignore[import-untyped]
from tests.workspace_isolation import isolated_cwd

from scripts.quality import osv_severity_gate as severity
from scripts.quality import scheduled_cve_scan as sweep

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "inventory" / "repos.yml"
WORKFLOW = ROOT / ".github" / "workflows" / "scheduled-cve-scan.yml"
REUSABLE_QUALITY = ROOT / ".github" / "workflows" / "reusable-quality.yml"
GENERATED_RULESETS = ROOT / "generated" / "rulesets"

FIXED_NOW = dt.datetime(2026, 8, 11, 5, 41, 0, tzinfo=dt.UTC)

VULNERABLE_REPORT: Dict[str, Any] = {
    "results": [
        {
            "source": {"path": "/repo/package-lock.json", "type": "lockfile"},
            "packages": [
                {
                    "package": {"name": "@angular/common", "version": "17.0.0", "ecosystem": "npm"},
                    "groups": [{"ids": ["GHSA-jhpw-976m-542j"], "max_severity": "8.8"}],
                    "vulnerabilities": [
                        {
                            "id": "GHSA-jhpw-976m-542j",
                            "affected": [{"ranges": [{"events": [{"introduced": "0"}, {"fixed": "17.3.12"}]}]}],
                        }
                    ],
                },
                {
                    "package": {"name": "brace-expansion", "version": "1.1.11", "ecosystem": "npm"},
                    "dependency_groups": ["dev"],
                    "groups": [{"ids": ["GHSA-rgw5-rvv9-x895"], "max_severity": "3.1"}],
                    "vulnerabilities": [
                        {
                            "id": "GHSA-rgw5-rvv9-x895",
                            "affected": [{"ranges": [{"events": [{"introduced": "0"}, {"fixed": "1.1.12"}]}]}],
                        }
                    ],
                },
            ],
        }
    ]
}

CLEAN_REPORT: Dict[str, Any] = {"results": []}


def _completed(
    args: Sequence[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> "subprocess.CompletedProcess[str]":
    """Build a ``CompletedProcess`` the way ``subprocess.run`` would."""
    return subprocess.CompletedProcess(list(args), returncode, stdout, stderr)


class FakeRunner:
    """Records every argv and replays scripted results.

    ``gh repo clone`` genuinely creates the destination directory (and, when
    ``config_repos`` names the repo, an ``osv-scanner.toml`` inside it) so the
    module's real filesystem probe for the caller's ignore file is exercised
    rather than mocked away.
    """

    def __init__(
        self,
        *,
        osv_results: Optional[List[Tuple[int, str]]] = None,
        clone_returncode: int = 0,
        issue_list: Optional[List[Any]] = None,
        issue_list_returncode: int = 0,
        create_stdout: str = "https://github.com/Prekzursil/quality-zero-platform/issues/512\n",
        label_returncode: int = 0,
        edit_returncode: int = 0,
        close_returncode: int = 0,
        config_repos: Sequence[str] = (),
    ) -> None:
        """Script the fake ``gh`` / ``osv-scanner`` responses for one test."""
        self.calls: List[List[str]] = []
        self.osv_results = list(osv_results or [(0, json.dumps(CLEAN_REPORT))])
        self.clone_returncode = clone_returncode
        self.issue_list = issue_list
        self.issue_list_returncode = issue_list_returncode
        self.create_stdout = create_stdout
        self.label_returncode = label_returncode
        self.edit_returncode = edit_returncode
        self.close_returncode = close_returncode
        self.config_repos = set(config_repos)
        self._osv_index = 0

    def __call__(self, args: Sequence[str], **_kwargs: Any) -> "subprocess.CompletedProcess[str]":
        """Dispatch on the argv shape, exactly as the real binaries would be."""
        argv = list(args)
        self.calls.append(argv)
        if argv[0] == "osv-scanner":
            return self._osv(argv)
        if argv[:3] == ["gh", "repo", "clone"]:
            return self._clone(argv)
        if argv[:3] == ["gh", "label", "create"]:
            return _completed(argv, returncode=self.label_returncode)
        if argv[:3] == ["gh", "issue", "list"]:
            payload = json.dumps(self.issue_list) if self.issue_list is not None else "[]"
            return _completed(argv, returncode=self.issue_list_returncode, stdout=payload)
        if argv[:3] == ["gh", "issue", "create"]:
            return _completed(argv, stdout=self.create_stdout)
        if argv[:3] == ["gh", "issue", "edit"]:
            return _completed(argv, returncode=self.edit_returncode)
        if argv[:3] == ["gh", "issue", "close"]:
            return _completed(argv, returncode=self.close_returncode)
        raise AssertionError(f"unscripted command: {argv}")  # pragma: no cover — test guard

    def _clone(self, argv: List[str]) -> "subprocess.CompletedProcess[str]":
        """Create the destination tree so the config probe has something to see."""
        if self.clone_returncode != 0:
            return _completed(argv, returncode=self.clone_returncode, stderr="clone failed\n")
        slug = argv[3]
        dest = Path(argv[4])
        dest.mkdir(parents=True, exist_ok=True)
        if slug in self.config_repos:
            (dest / "osv-scanner.toml").write_text("[[IgnoredVulns]]\n", encoding="utf-8")
        return _completed(argv)

    def _osv(self, argv: List[str]) -> "subprocess.CompletedProcess[str]":
        """Replay the next scripted scan result, holding the last one."""
        index = min(self._osv_index, len(self.osv_results) - 1)
        self._osv_index += 1
        code, stdout = self.osv_results[index]
        return _completed(argv, returncode=code, stdout=stdout)

    def argv_starting(self, *prefix: str) -> List[List[str]]:
        """Every recorded argv whose leading tokens match ``prefix``."""
        size = len(prefix)
        return [argv for argv in self.calls if argv[:size] == list(prefix)]


class NoopSleeper:
    """Records the requested backoff instead of blocking the test suite."""

    def __init__(self) -> None:
        """Start with an empty record."""
        self.slept: List[float] = []

    def __call__(self, seconds: float) -> None:
        """Record, never sleep."""
        self.slept.append(seconds)


def _scan(status: sweep.ScanStatus, **overrides: Any) -> sweep.RepoScan:
    """Build a ``RepoScan`` in the given status with sensible defaults."""
    blocking, demoted = severity.classify(
        severity.iter_findings(VULNERABLE_REPORT),
        severity.SEVERITY_FLOOR,
    )
    defaults: Dict[str, Any] = {
        "slug": "Prekzursil/momentstudio",
        "status": status,
        "exit_code": 0,
        "detail": "",
        "blocking": (),
        "demoted": (),
        "attempts": 1,
        "config_applied": False,
    }
    if status is sweep.ScanStatus.FINDINGS:
        defaults.update({"exit_code": 1, "blocking": tuple(blocking), "demoted": tuple(demoted)})
    defaults.update(overrides)
    return sweep.RepoScan(**defaults)


# ---------------------------------------------------------------------------
# 1. The exit-code contract — gate 6's mapping, verbatim, both states.
# ---------------------------------------------------------------------------


class ExitCodeContractTests(unittest.TestCase):
    """A scan error is never a vulnerability verdict and never a clean result."""

    def test_zero_is_clean(self) -> None:
        """Exit 0 = the scan completed and found nothing."""
        status, detail = sweep.classify_exit_code(0)
        self.assertIs(status, sweep.ScanStatus.CLEAN)
        self.assertTrue(detail)

    def test_one_is_findings(self) -> None:
        """Exit 1 is the ONLY vulnerability code."""
        status, _detail = sweep.classify_exit_code(1)
        self.assertIs(status, sweep.ScanStatus.FINDINGS)

    def test_one_two_eight_is_nothing_to_scan(self) -> None:
        """Exit 128 (ErrNoPackagesFound) is not a finding and not an error."""
        status, _detail = sweep.classify_exit_code(128)
        self.assertIs(status, sweep.ScanStatus.NOTHING_TO_SCAN)

    def test_scan_error_codes_are_scan_errors(self) -> None:
        """127 / 129 / 130 each mean the scan did NOT complete."""
        for code in (127, 129, 130):
            with self.subTest(code=code):
                status, detail = sweep.classify_exit_code(code)
                self.assertIs(status, sweep.ScanStatus.SCAN_ERROR)
                self.assertTrue(detail, "a scan error must explain itself")

    def test_each_scan_error_code_has_its_own_explanation(self) -> None:
        """127, 129 and 130 have distinct causes, so distinct detail text."""
        details = {code: sweep.classify_exit_code(code)[1] for code in (127, 129, 130)}
        self.assertEqual(len(set(details.values())), 3, details)

    def test_undocumented_code_is_a_scan_error_not_a_finding(self) -> None:
        """An undocumented exit fails closed as a scan error."""
        status, detail = sweep.classify_exit_code(42)
        self.assertIs(status, sweep.ScanStatus.SCAN_ERROR)
        self.assertIn("42", detail)

    def test_clean_result_predicate_both_states(self) -> None:
        """BOTH-STATES: only CLEAN / NOTHING_TO_SCAN may clear an issue."""
        self.assertTrue(sweep.ScanStatus.CLEAN.is_clean_result)
        self.assertTrue(sweep.ScanStatus.NOTHING_TO_SCAN.is_clean_result)
        self.assertFalse(sweep.ScanStatus.FINDINGS.is_clean_result)
        self.assertFalse(sweep.ScanStatus.SCAN_ERROR.is_clean_result)

    def test_retryable_exits_are_the_transient_pair_only(self) -> None:
        """127/129 are transient; 130 (invalid config) is deterministic."""
        self.assertTrue(sweep.is_retryable_exit(127))
        self.assertTrue(sweep.is_retryable_exit(129))
        self.assertFalse(sweep.is_retryable_exit(130))
        self.assertFalse(sweep.is_retryable_exit(0))
        self.assertFalse(sweep.is_retryable_exit(1))

    def test_pinned_version_matches_the_gate(self) -> None:
        """The sweep and gate 6 must scan with the SAME pinned binary.

        Different versions have different advisory-matching behaviour, so a
        sweep on another version would predict a "would block" verdict the
        gate does not actually reach.
        """
        text = REUSABLE_QUALITY.read_text(encoding="utf-8")
        self.assertIn(f"osv-scanner/releases/download/{sweep.OSV_SCANNER_VERSION}/", text)


# ---------------------------------------------------------------------------
# 2. The roster comes from inventory/repos.yml — never a hardcoded list.
# ---------------------------------------------------------------------------


class FleetRosterTests(unittest.TestCase):
    """The fleet is read from the inventory file, and stays in step with it."""

    def test_reads_every_slug_from_the_real_inventory(self) -> None:
        """The roster equals the inventory's own slug set, exactly."""
        declared = {
            entry["slug"]
            for entry in yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))["repos"]
            if entry.get("slug")
        }
        self.assertEqual(set(sweep.load_fleet_slugs(INVENTORY)), declared)
        self.assertGreater(len(declared), 1, "the inventory should hold the whole fleet")

    def test_no_slug_is_hardcoded_in_the_module(self) -> None:
        """Detector-controlled: a real slug appears in the inventory, not the code."""
        source = (ROOT / "scripts" / "quality" / "scheduled_cve_scan.py").read_text(encoding="utf-8")
        inventory_text = INVENTORY.read_text(encoding="utf-8")
        needle = "Prekzursil/momentstudio"
        self.assertIn(needle, inventory_text, "detector control: the needle must exist somewhere")
        self.assertNotIn(needle, source, "repo slugs must come from the inventory, not the source")

    def test_only_filter_narrows_the_roster(self) -> None:
        """``--only`` accepts a full slug and keeps inventory ordering."""
        slugs = sweep.load_fleet_slugs(INVENTORY, only=["Prekzursil/momentstudio"])
        self.assertEqual(slugs, ["Prekzursil/momentstudio"])

    def test_only_filter_accepts_a_bare_repo_name(self) -> None:
        """Operators type the short name; both forms resolve."""
        self.assertEqual(sweep.load_fleet_slugs(INVENTORY, only=["momentstudio"]), ["Prekzursil/momentstudio"])

    def test_only_filter_rejects_an_unknown_name(self) -> None:
        """An unknown ``--only`` value is an error, not a silent empty sweep."""
        with self.assertRaises(sweep.FleetRosterError):
            sweep.load_fleet_slugs(INVENTORY, only=["not-a-fleet-repo"])

    def test_blank_only_entries_are_ignored(self) -> None:
        """An empty dispatch input must not narrow the sweep to nothing."""
        self.assertEqual(sweep.load_fleet_slugs(INVENTORY, only=["", "  "]), sweep.load_fleet_slugs(INVENTORY))

    def test_missing_inventory_is_an_error(self) -> None:
        """A missing roster file never degrades to "the fleet is empty"."""
        with self.assertRaises(sweep.FleetRosterError):
            sweep.load_fleet_slugs(ROOT / "inventory" / "does-not-exist.yml")

    def test_malformed_inventory_is_an_error(self) -> None:
        """A roster with no usable slugs is an error, not an empty sweep."""
        with isolated_cwd() as tmp:
            path = tmp / "repos.yml"
            path.write_text("version: 1\nrepos: []\n", encoding="utf-8")
            with self.assertRaises(sweep.FleetRosterError):
                sweep.load_fleet_slugs(path)


# ---------------------------------------------------------------------------
# 3. scan_repo — clone, scan, retry, classify.
# ---------------------------------------------------------------------------


class ScanRepoTests(unittest.TestCase):
    """One repo, end to end, against a scripted ``gh`` + ``osv-scanner``."""

    def _scan(self, runner: FakeRunner, *, sleeper: Optional[NoopSleeper] = None, **kwargs: Any) -> sweep.RepoScan:
        """Run ``scan_repo`` inside a throwaway workdir."""
        with isolated_cwd() as tmp:
            return sweep.scan_repo(
                "Prekzursil/momentstudio",
                workdir=tmp,
                floor=severity.SEVERITY_FLOOR,
                runner=runner,
                sleeper=sleeper or NoopSleeper(),
                **kwargs,
            )

    def test_clean_scan_is_clean(self) -> None:
        """Exit 0 yields CLEAN with no findings."""
        scan = self._scan(FakeRunner(osv_results=[(0, json.dumps(CLEAN_REPORT))]))
        self.assertIs(scan.status, sweep.ScanStatus.CLEAN)
        self.assertEqual(scan.blocking, ())
        self.assertEqual(scan.demoted, ())

    def test_findings_are_split_into_t0_and_t2(self) -> None:
        """Exit 1 re-reads the report and applies the SAME classifier as the gate."""
        scan = self._scan(FakeRunner(osv_results=[(1, json.dumps(VULNERABLE_REPORT))]))
        self.assertIs(scan.status, sweep.ScanStatus.FINDINGS)
        self.assertEqual([f.package for f in scan.blocking], ["@angular/common"])
        self.assertEqual([f.package for f in scan.demoted], ["brace-expansion"])

    def test_nothing_to_scan_is_not_a_finding(self) -> None:
        """Exit 128 on a zero-dependency repo is a pass, not an error."""
        scan = self._scan(FakeRunner(osv_results=[(128, "")]))
        self.assertIs(scan.status, sweep.ScanStatus.NOTHING_TO_SCAN)

    def test_clone_failure_is_a_scan_error(self) -> None:
        """A repo we could not clone was NOT scanned, so it is never clean."""
        runner = FakeRunner(clone_returncode=1)
        scan = self._scan(runner)
        self.assertIs(scan.status, sweep.ScanStatus.SCAN_ERROR)
        self.assertFalse(scan.status.is_clean_result)
        self.assertEqual(runner.argv_starting("osv-scanner"), [], "no scan should be attempted")

    def test_transient_exit_is_retried_then_succeeds(self) -> None:
        """127 retries with gate 6's ``attempt * 20`` backoff, then passes."""
        sleeper = NoopSleeper()
        runner = FakeRunner(osv_results=[(127, ""), (0, json.dumps(CLEAN_REPORT))])
        scan = self._scan(runner, sleeper=sleeper)
        self.assertIs(scan.status, sweep.ScanStatus.CLEAN)
        self.assertEqual(scan.attempts, 2)
        self.assertEqual(sleeper.slept, [20])

    def test_transient_exit_exhausts_attempts_and_stays_an_error(self) -> None:
        """Three failures is a scan error - never downgraded to clean."""
        sleeper = NoopSleeper()
        runner = FakeRunner(osv_results=[(129, ""), (129, ""), (129, "")])
        scan = self._scan(runner, sleeper=sleeper)
        self.assertIs(scan.status, sweep.ScanStatus.SCAN_ERROR)
        self.assertEqual(scan.attempts, 3)
        self.assertEqual(sleeper.slept, [20, 40])

    def test_invalid_config_is_not_retried(self) -> None:
        """130 is deterministic; retrying it only burns runner minutes."""
        sleeper = NoopSleeper()
        runner = FakeRunner(osv_results=[(130, "")])
        scan = self._scan(runner, sleeper=sleeper)
        self.assertIs(scan.status, sweep.ScanStatus.SCAN_ERROR)
        self.assertEqual(scan.attempts, 1)
        self.assertEqual(sleeper.slept, [])

    def test_unparseable_report_on_exit_one_is_a_scan_error(self) -> None:
        """We could not classify, so we must not claim a verdict either way."""
        scan = self._scan(FakeRunner(osv_results=[(1, "{not json")]))
        self.assertIs(scan.status, sweep.ScanStatus.SCAN_ERROR)
        self.assertIn("could not be parsed", scan.detail)

    def test_callers_ignore_file_is_applied_when_present(self) -> None:
        """``--config`` mirrors gate 6 so T0 is a faithful would-block prediction."""
        runner = FakeRunner(
            osv_results=[(0, json.dumps(CLEAN_REPORT))],
            config_repos=["Prekzursil/momentstudio"],
        )
        scan = self._scan(runner)
        self.assertTrue(scan.config_applied)
        argv = runner.argv_starting("osv-scanner")[0]
        self.assertTrue(any(item.startswith("--config=") for item in argv), argv)

    def test_no_ignore_file_means_no_config_flag(self) -> None:
        """BOTH-STATES: without the file the flag is absent (osv rejects a missing config)."""
        runner = FakeRunner(osv_results=[(0, json.dumps(CLEAN_REPORT))])
        scan = self._scan(runner)
        self.assertFalse(scan.config_applied)
        argv = runner.argv_starting("osv-scanner")[0]
        self.assertFalse(any(item.startswith("--config=") for item in argv), argv)

    def test_scan_is_json_recursive_and_source_scoped(self) -> None:
        """The scan shape matches gate 6: ``scan source --recursive --format json``."""
        runner = FakeRunner()
        self._scan(runner)
        argv = runner.argv_starting("osv-scanner")[0]
        self.assertEqual(argv[:3], ["osv-scanner", "scan", "source"])
        self.assertIn("--recursive", argv)
        self.assertIn("--format", argv)
        self.assertIn("json", argv)

    def test_clone_is_shallow_and_single_branch(self) -> None:
        """A full fleet clone every night is wasted minutes and disk."""
        runner = FakeRunner()
        self._scan(runner)
        argv = runner.argv_starting("gh", "repo", "clone")[0]
        self.assertIn("--depth=1", argv)
        self.assertIn("--single-branch", argv)


# ---------------------------------------------------------------------------
# 4. Issue body rendering — the both-states proof.
# ---------------------------------------------------------------------------


class RenderIssueBodyTests(unittest.TestCase):
    """The body must distinguish act-now from for-information, and states."""

    def _body(self, status: sweep.ScanStatus, **overrides: Any) -> str:
        """Render a body for the given status."""
        return sweep.render_issue_body(
            _scan(status, **overrides),
            floor=severity.SEVERITY_FLOOR,
            generated_at=FIXED_NOW,
        )

    def test_findings_body_separates_t0_from_t2(self) -> None:
        """T0 names the fix; T2 names the demotion reason."""
        body = self._body(sweep.ScanStatus.FINDINGS)
        self.assertIn("T0", body)
        self.assertIn("T2", body)
        self.assertIn("@angular/common", body)
        self.assertIn("fixed in 17.3.12", body)
        self.assertIn("brace-expansion", body)
        self.assertIn("dev-only", body)

    def test_findings_body_counts_each_tier(self) -> None:
        """Counts let a reader triage without reading every row."""
        body = self._body(sweep.ScanStatus.FINDINGS)
        self.assertIn("T0 - WOULD BLOCK (1)", body)
        self.assertIn("T2 - INVENTORY (1)", body)

    def test_body_carries_the_stable_marker_and_pinned_version(self) -> None:
        """The marker identifies our own body; the version pins the evidence."""
        body = self._body(sweep.ScanStatus.FINDINGS)
        self.assertIn(sweep.BODY_MARKER, body)
        self.assertIn(sweep.OSV_SCANNER_VERSION, body)

    def test_body_is_timestamped_from_the_injected_clock(self) -> None:
        """Determinism: the timestamp comes from the caller, never ``now()``."""
        self.assertIn("2026-08-11T05:41:00+00:00", self._body(sweep.ScanStatus.FINDINGS))

    def test_body_states_whether_the_callers_ignore_file_was_applied(self) -> None:
        """A suppressed finding must not read as an absent finding."""
        applied = self._body(sweep.ScanStatus.FINDINGS, config_applied=True)
        absent = self._body(sweep.ScanStatus.FINDINGS, config_applied=False)
        self.assertIn("osv-scanner.toml", applied)
        self.assertIn("osv-scanner.toml", absent)
        self.assertNotEqual(applied, absent)

    def test_body_says_it_is_not_a_gate(self) -> None:
        """Nobody should mistake this issue for a blocking check."""
        self.assertIn("not a gate", self._body(sweep.ScanStatus.FINDINGS).lower())

    def test_both_states_findings_body_differs_from_clean_body(self) -> None:
        """BOTH-STATES PROOF: the two runs cannot render the same body."""
        findings = self._body(sweep.ScanStatus.FINDINGS)
        clean = self._body(sweep.ScanStatus.CLEAN)
        self.assertNotEqual(findings, clean)
        self.assertIn("@angular/common", findings)
        self.assertNotIn("@angular/common", clean)
        self.assertIn("No dependency advisories", clean)
        self.assertNotIn("No dependency advisories", findings)

    def test_nothing_to_scan_body_says_so_explicitly(self) -> None:
        """ "No packages" is a different fact from "no advisories"."""
        body = self._body(sweep.ScanStatus.NOTHING_TO_SCAN)
        self.assertIn("no dependency manifests", body.lower())

    def test_scan_error_body_never_claims_a_clean_result(self) -> None:
        """The single most important sentence in the module."""
        body = self._body(sweep.ScanStatus.SCAN_ERROR, exit_code=129, detail="the OSV.dev API was unreachable")
        self.assertIn("SCAN ERROR", body)
        self.assertIn("NOT a vulnerability verdict", body)
        self.assertIn("not a clean result", body)
        self.assertIn("the OSV.dev API was unreachable", body)

    def test_run_url_is_linked_when_supplied(self) -> None:
        """The run that produced the body is the provenance for it."""
        body = sweep.render_issue_body(
            _scan(sweep.ScanStatus.FINDINGS),
            floor=severity.SEVERITY_FLOOR,
            generated_at=FIXED_NOW,
            run_url="https://github.com/x/y/actions/runs/1",
        )
        self.assertIn("https://github.com/x/y/actions/runs/1", body)

    def test_rows_are_capped_and_the_remainder_is_disclosed(self) -> None:
        """A 500-finding repo must not produce an unreadable, over-limit body."""
        packages = [
            {
                "package": {"name": f"pkg-{index}", "version": "1.0.0", "ecosystem": "npm"},
                "dependency_groups": ["dev"],
                "groups": [{"ids": [f"GHSA-{index}"], "max_severity": "1.0"}],
                "vulnerabilities": [],
            }
            for index in range(sweep.MAX_ROWS_PER_TIER + 3)
        ]
        document = {"results": [{"source": {"path": "lock.json"}, "packages": packages}]}
        blocking, demoted = severity.classify(severity.iter_findings(document), severity.SEVERITY_FLOOR)
        body = sweep.render_issue_body(
            _scan(sweep.ScanStatus.FINDINGS, blocking=tuple(blocking), demoted=tuple(demoted)),
            floor=severity.SEVERITY_FLOOR,
            generated_at=FIXED_NOW,
        )
        self.assertIn("3 more", body)

    def test_body_is_truncated_below_the_github_limit(self) -> None:
        """GitHub hard-caps an issue body; a rejected update is a silent gap."""
        body = sweep.render_issue_body(
            _scan(sweep.ScanStatus.FINDINGS),
            floor=severity.SEVERITY_FLOOR,
            generated_at=FIXED_NOW,
            max_body_chars=200,
        )
        self.assertLessEqual(len(body), 200)
        self.assertIn("truncated", body)

    def test_default_body_cap_is_under_the_github_ceiling(self) -> None:
        """The shipped cap must be below GitHub's 65536-character limit."""
        self.assertLess(sweep.MAX_BODY_CHARS, 65536)


# ---------------------------------------------------------------------------
# 5. Reconciliation — one issue per repo, updated in place, closed when clear.
# ---------------------------------------------------------------------------


OPEN_ISSUE = [{"number": 77, "title": "[alert:cve-watch] Prekzursil/momentstudio", "state": "OPEN"}]


class ReconcileTests(unittest.TestCase):
    """The reconciler is the part that must be idempotent and safe to re-run."""

    def _reconcile(self, scan: sweep.RepoScan, runner: FakeRunner, **kwargs: Any) -> sweep.RepoOutcome:
        """Reconcile one scan against a scripted ``gh``."""
        return sweep.reconcile(
            scan,
            platform_slug="Prekzursil/quality-zero-platform",
            floor=severity.SEVERITY_FLOOR,
            generated_at=FIXED_NOW,
            runner=runner,
            **kwargs,
        )

    def test_findings_with_no_existing_issue_creates_one(self) -> None:
        """First sighting opens the tracking issue."""
        runner = FakeRunner()
        outcome = self._reconcile(_scan(sweep.ScanStatus.FINDINGS), runner)
        self.assertIs(outcome.action, sweep.IssueAction.CREATED)
        self.assertEqual(outcome.issue_number, 512)
        self.assertEqual(len(runner.argv_starting("gh", "issue", "create")), 1)

    def test_findings_with_an_existing_issue_updates_in_place(self) -> None:
        """THE core requirement: update, never open a second issue."""
        runner = FakeRunner(issue_list=OPEN_ISSUE)
        outcome = self._reconcile(_scan(sweep.ScanStatus.FINDINGS), runner)
        self.assertIs(outcome.action, sweep.IssueAction.UPDATED)
        self.assertEqual(outcome.issue_number, 77)
        self.assertEqual(runner.argv_starting("gh", "issue", "create"), [])
        edits = runner.argv_starting("gh", "issue", "edit")
        self.assertEqual(len(edits), 1)
        self.assertIn("77", edits[0])

    def test_clean_closes_the_open_issue(self) -> None:
        """ "Close or clear the issue when findings clear."""
        runner = FakeRunner(issue_list=OPEN_ISSUE)
        outcome = self._reconcile(_scan(sweep.ScanStatus.CLEAN), runner)
        self.assertIs(outcome.action, sweep.IssueAction.CLOSED)
        self.assertEqual(outcome.issue_number, 77)
        closes = runner.argv_starting("gh", "issue", "close")
        self.assertEqual(len(closes), 1)
        self.assertIn("--comment", closes[0])

    def test_clean_with_no_issue_is_a_noop(self) -> None:
        """A permanently clean repo generates no traffic at all."""
        runner = FakeRunner()
        outcome = self._reconcile(_scan(sweep.ScanStatus.CLEAN), runner)
        self.assertIs(outcome.action, sweep.IssueAction.NOOP)
        self.assertEqual(runner.argv_starting("gh", "issue", "close"), [])

    def test_nothing_to_scan_also_clears_the_issue(self) -> None:
        """A repo that lost its manifests has no advisories to track."""
        runner = FakeRunner(issue_list=OPEN_ISSUE)
        outcome = self._reconcile(_scan(sweep.ScanStatus.NOTHING_TO_SCAN), runner)
        self.assertIs(outcome.action, sweep.IssueAction.CLOSED)

    def test_scan_error_touches_nothing(self) -> None:
        """A failed scan must not close, create, or overwrite anything."""
        runner = FakeRunner(issue_list=OPEN_ISSUE)
        outcome = self._reconcile(
            _scan(sweep.ScanStatus.SCAN_ERROR, exit_code=127, detail="resolver outage"),
            runner,
        )
        self.assertIs(outcome.action, sweep.IssueAction.SKIPPED_SCAN_ERROR)
        self.assertEqual(runner.argv_starting("gh", "issue", "create"), [])
        self.assertEqual(runner.argv_starting("gh", "issue", "edit"), [])
        self.assertEqual(runner.argv_starting("gh", "issue", "close"), [])

    def test_label_is_ensured_before_creating(self) -> None:
        """``gh issue create --label`` fails outright on an unknown label."""
        runner = FakeRunner()
        self._reconcile(_scan(sweep.ScanStatus.FINDINGS), runner)
        labels = runner.argv_starting("gh", "label", "create")
        self.assertEqual(len(labels), 1)
        self.assertIn(sweep.ALERT_LABEL, labels[0])
        self.assertIn("--force", labels[0])

    def test_label_failure_does_not_abort_the_repo(self) -> None:
        """A label race is not a reason to lose the finding."""
        runner = FakeRunner(label_returncode=1)
        outcome = self._reconcile(_scan(sweep.ScanStatus.FINDINGS), runner)
        self.assertIs(outcome.action, sweep.IssueAction.CREATED)

    def test_dry_run_makes_no_write_call(self) -> None:
        """Manual dispatch defaults to dry-run; it must be inert."""
        runner = FakeRunner(issue_list=OPEN_ISSUE)
        outcome = self._reconcile(_scan(sweep.ScanStatus.FINDINGS), runner, dry_run=True)
        self.assertIs(outcome.action, sweep.IssueAction.UPDATED)
        self.assertTrue(outcome.dry_run)
        self.assertEqual(runner.argv_starting("gh", "issue", "edit"), [])
        self.assertEqual(runner.argv_starting("gh", "label", "create"), [])

    def test_dry_run_clean_reports_the_close_it_would_make(self) -> None:
        """Dry-run must still show the clearing action."""
        runner = FakeRunner(issue_list=OPEN_ISSUE)
        outcome = self._reconcile(_scan(sweep.ScanStatus.CLEAN), runner, dry_run=True)
        self.assertIs(outcome.action, sweep.IssueAction.CLOSED)
        self.assertEqual(runner.argv_starting("gh", "issue", "close"), [])

    def test_dry_run_clean_with_no_issue_is_a_noop(self) -> None:
        """Nothing open, nothing to clear, nothing to report."""
        outcome = self._reconcile(_scan(sweep.ScanStatus.CLEAN), FakeRunner(), dry_run=True)
        self.assertIs(outcome.action, sweep.IssueAction.NOOP)

    def test_issue_lookup_failure_never_creates_a_duplicate(self) -> None:
        """If we cannot read the issue state we must not guess it."""
        runner = FakeRunner(issue_list_returncode=1)
        with self.assertRaises(sweep.GhCommandError):
            self._reconcile(_scan(sweep.ScanStatus.FINDINGS), runner)
        self.assertEqual(runner.argv_starting("gh", "issue", "create"), [])

    def test_unparseable_issue_list_is_an_error_not_an_empty_list(self) -> None:
        """A broken payload must not read as "no issue exists"."""
        runner = FakeRunner()
        runner.issue_list = None
        original = runner.__call__

        def broken(args: Sequence[str], **kwargs: Any) -> "subprocess.CompletedProcess[str]":
            """Return junk for the issue-list call only."""
            argv = list(args)
            if argv[:3] == ["gh", "issue", "list"]:
                runner.calls.append(argv)
                return _completed(argv, stdout="{not json")
            return original(args, **kwargs)

        with self.assertRaises(sweep.GhCommandError):
            sweep.reconcile(
                _scan(sweep.ScanStatus.FINDINGS),
                platform_slug="Prekzursil/quality-zero-platform",
                floor=severity.SEVERITY_FLOOR,
                generated_at=FIXED_NOW,
                runner=broken,
            )

    def test_non_list_issue_payload_is_an_error(self) -> None:
        """A JSON object where an array was promised is a broken detector."""
        runner = FakeRunner(issue_list={"number": 1})
        with self.assertRaises(sweep.GhCommandError):
            self._reconcile(_scan(sweep.ScanStatus.FINDINGS), runner)

    def test_a_differently_titled_issue_is_not_reused(self) -> None:
        """Search is fuzzy; the title match must be exact."""
        runner = FakeRunner(issue_list=[{"number": 5, "title": "[alert:cve-watch] Prekzursil/other", "state": "OPEN"}])
        outcome = self._reconcile(_scan(sweep.ScanStatus.FINDINGS), runner)
        self.assertIs(outcome.action, sweep.IssueAction.CREATED)

    def test_non_mapping_issue_entries_are_skipped(self) -> None:
        """Stray payload members must not crash the sweep."""
        runner = FakeRunner(
            issue_list=[
                "nope",
                {"number": 77, "title": sweep.issue_title(_scan(sweep.ScanStatus.FINDINGS).slug), "state": "OPEN"},
            ]
        )
        outcome = self._reconcile(_scan(sweep.ScanStatus.FINDINGS), runner)
        self.assertIs(outcome.action, sweep.IssueAction.UPDATED)

    def test_create_output_without_a_number_still_reports_created(self) -> None:
        """A parse miss on the URL must not look like a failure."""
        runner = FakeRunner(create_stdout="no url here\n")
        outcome = self._reconcile(_scan(sweep.ScanStatus.FINDINGS), runner)
        self.assertIs(outcome.action, sweep.IssueAction.CREATED)
        self.assertEqual(outcome.issue_number, 0)

    def test_create_output_empty_still_reports_created(self) -> None:
        """Empty stdout is tolerated for the same reason."""
        runner = FakeRunner(create_stdout="")
        outcome = self._reconcile(_scan(sweep.ScanStatus.FINDINGS), runner)
        self.assertEqual(outcome.issue_number, 0)

    def test_edit_failure_is_reported_as_an_error(self) -> None:
        """A failed update must not be logged as a successful one."""
        runner = FakeRunner(issue_list=OPEN_ISSUE, edit_returncode=1)
        with self.assertRaises(sweep.GhCommandError):
            self._reconcile(_scan(sweep.ScanStatus.FINDINGS), runner)

    def test_close_failure_is_reported_as_an_error(self) -> None:
        """Same for a failed clear."""
        runner = FakeRunner(issue_list=OPEN_ISSUE, close_returncode=1)
        with self.assertRaises(sweep.GhCommandError):
            self._reconcile(_scan(sweep.ScanStatus.CLEAN), runner)

    def test_issue_title_is_stable_and_dedupable(self) -> None:
        """The title IS the dedupe key; it must not drift."""
        self.assertEqual(
            sweep.issue_title("Prekzursil/env-inspector"),
            f"[{sweep.ALERT_LABEL}] Prekzursil/env-inspector",
        )


# ---------------------------------------------------------------------------
# 6. Sweep + CLI.
# ---------------------------------------------------------------------------


class RunSweepTests(unittest.TestCase):
    """The whole-fleet loop, including idempotency across two runs."""

    def _sweep(self, slugs: Sequence[str], runner: FakeRunner, **kwargs: Any) -> List[sweep.RepoOutcome]:
        """Run one sweep in a throwaway workdir."""
        with isolated_cwd() as tmp:
            return sweep.run_sweep(
                slugs,
                workdir=tmp,
                platform_slug="Prekzursil/quality-zero-platform",
                floor=severity.SEVERITY_FLOOR,
                generated_at=FIXED_NOW,
                runner=runner,
                sleeper=NoopSleeper(),
                **kwargs,
            )

    def test_every_repo_produces_exactly_one_outcome(self) -> None:
        """No repo is silently dropped from the sweep."""
        runner = FakeRunner(osv_results=[(0, json.dumps(CLEAN_REPORT))])
        outcomes = self._sweep(["Prekzursil/a", "Prekzursil/b", "Prekzursil/c"], runner)
        self.assertEqual([o.slug for o in outcomes], ["Prekzursil/a", "Prekzursil/b", "Prekzursil/c"])

    def test_a_gh_failure_on_one_repo_does_not_abort_the_others(self) -> None:
        """A fleet sweep must be resilient per repo."""
        runner = FakeRunner(osv_results=[(1, json.dumps(VULNERABLE_REPORT))], issue_list_returncode=1)
        outcomes = self._sweep(["Prekzursil/a", "Prekzursil/b"], runner)
        self.assertEqual([o.action for o in outcomes], [sweep.IssueAction.FAILED, sweep.IssueAction.FAILED])
        self.assertTrue(all(o.detail for o in outcomes))

    def test_idempotent_across_two_runs(self) -> None:
        """Re-running with the issue now open UPDATES it; it never duplicates."""
        first = FakeRunner(osv_results=[(1, json.dumps(VULNERABLE_REPORT))])
        self.assertIs(self._sweep(["Prekzursil/a"], first)[0].action, sweep.IssueAction.CREATED)

        second = FakeRunner(
            osv_results=[(1, json.dumps(VULNERABLE_REPORT))],
            issue_list=[{"number": 512, "title": sweep.issue_title("Prekzursil/a"), "state": "OPEN"}],
        )
        outcome = self._sweep(["Prekzursil/a"], second)[0]
        self.assertIs(outcome.action, sweep.IssueAction.UPDATED)
        self.assertEqual(second.argv_starting("gh", "issue", "create"), [])

    def test_each_repo_is_cloned_into_its_own_directory(self) -> None:
        """Two repos must not share a working tree."""
        runner = FakeRunner()
        self._sweep(["Prekzursil/a", "Prekzursil/b"], runner)
        destinations = [argv[4] for argv in runner.argv_starting("gh", "repo", "clone")]
        self.assertEqual(len(set(destinations)), 2, destinations)

    def test_summary_reports_every_status_and_action(self) -> None:
        """The step summary is the operator's only view of a scheduled run."""
        outcomes = [
            sweep.RepoOutcome("Prekzursil/a", sweep.ScanStatus.FINDINGS, sweep.IssueAction.CREATED, 1, "", False),
            sweep.RepoOutcome("Prekzursil/b", sweep.ScanStatus.CLEAN, sweep.IssueAction.CLOSED, 2, "", False),
            sweep.RepoOutcome(
                "Prekzursil/c",
                sweep.ScanStatus.SCAN_ERROR,
                sweep.IssueAction.SKIPPED_SCAN_ERROR,
                0,
                "resolver outage",
                False,
            ),
        ]
        text = sweep.render_summary(outcomes)
        for needle in ("Prekzursil/a", "Prekzursil/b", "Prekzursil/c", "created", "closed", "resolver outage"):
            self.assertIn(needle, text)

    def test_summary_of_an_empty_sweep_says_so(self) -> None:
        """An empty table is worse than an explicit zero."""
        self.assertIn("no repositories", sweep.render_summary([]).lower())


class MainTests(unittest.TestCase):
    """CLI behaviour: exit codes, JSON output, summary file, dry-run."""

    def _run(self, argv: List[str], runner: FakeRunner) -> Tuple[int, str]:
        """Invoke ``main`` with an injected runner and captured output."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sweep.main(argv, runner=runner, sleeper=NoopSleeper(), now=FIXED_NOW)
        return code, out.getvalue() + err.getvalue()

    def test_clean_fleet_exits_zero(self) -> None:
        """Nothing to report is a successful run."""
        runner = FakeRunner(osv_results=[(0, json.dumps(CLEAN_REPORT))])
        with isolated_cwd() as tmp:
            code, _text = self._run(
                ["--inventory", str(INVENTORY), "--only", "momentstudio", "--workdir", str(tmp)],
                runner,
            )
        self.assertEqual(code, sweep.EXIT_OK)

    def test_findings_exit_zero_because_this_is_not_a_gate(self) -> None:
        """Findings are tracked in an issue, not turned into a red check."""
        runner = FakeRunner(osv_results=[(1, json.dumps(VULNERABLE_REPORT))])
        with isolated_cwd() as tmp:
            code, text = self._run(
                ["--inventory", str(INVENTORY), "--only", "momentstudio", "--workdir", str(tmp)],
                runner,
            )
        self.assertEqual(code, sweep.EXIT_OK)
        self.assertIn("findings", text)

    def test_scan_error_exits_non_zero(self) -> None:
        """An incomplete sweep must be visible in the Actions tab."""
        runner = FakeRunner(osv_results=[(130, "")])
        with isolated_cwd() as tmp:
            code, text = self._run(
                ["--inventory", str(INVENTORY), "--only", "momentstudio", "--workdir", str(tmp)],
                runner,
            )
        self.assertEqual(code, sweep.EXIT_INCOMPLETE)
        self.assertIn("scan-error", text)

    def test_gh_failure_exits_non_zero(self) -> None:
        """A reconcile failure is also an incomplete sweep."""
        runner = FakeRunner(osv_results=[(1, json.dumps(VULNERABLE_REPORT))], issue_list_returncode=1)
        with isolated_cwd() as tmp:
            code, _text = self._run(
                ["--inventory", str(INVENTORY), "--only", "momentstudio", "--workdir", str(tmp)],
                runner,
            )
        self.assertEqual(code, sweep.EXIT_INCOMPLETE)

    def test_json_output_is_machine_readable(self) -> None:
        """``--json`` gives the dashboard a stable shape."""
        runner = FakeRunner(osv_results=[(1, json.dumps(VULNERABLE_REPORT))])
        with isolated_cwd() as tmp:
            _code, text = self._run(
                ["--inventory", str(INVENTORY), "--only", "momentstudio", "--workdir", str(tmp), "--json"],
                runner,
            )
        payload = json.loads(text)
        self.assertEqual(payload["repos"][0]["slug"], "Prekzursil/momentstudio")
        self.assertEqual(payload["repos"][0]["status"], "findings")
        self.assertEqual(payload["repos"][0]["t0"], 1)
        self.assertEqual(payload["repos"][0]["t2"], 1)

    def test_summary_file_is_appended(self) -> None:
        """The workflow points ``--summary-file`` at ``GITHUB_STEP_SUMMARY``."""
        runner = FakeRunner(osv_results=[(0, json.dumps(CLEAN_REPORT))])
        with isolated_cwd() as tmp:
            summary = tmp / "summary.md"
            summary.write_text("pre-existing\n", encoding="utf-8")
            self._run(
                [
                    "--inventory",
                    str(INVENTORY),
                    "--only",
                    "momentstudio",
                    "--workdir",
                    str(tmp),
                    "--summary-file",
                    str(summary),
                ],
                runner,
            )
            text = summary.read_text(encoding="utf-8")
        self.assertIn("pre-existing", text)
        self.assertIn("Prekzursil/momentstudio", text)

    def test_dry_run_makes_no_write_call(self) -> None:
        """The dispatch default must be inert."""
        runner = FakeRunner(osv_results=[(1, json.dumps(VULNERABLE_REPORT))])
        with isolated_cwd() as tmp:
            code, _text = self._run(
                ["--inventory", str(INVENTORY), "--only", "momentstudio", "--workdir", str(tmp), "--dry-run"],
                runner,
            )
        self.assertEqual(code, sweep.EXIT_OK)
        self.assertEqual(runner.argv_starting("gh", "issue", "create"), [])

    def test_bad_only_value_exits_with_the_config_code(self) -> None:
        """A typo in the dispatch input fails loudly, not silently empty."""
        runner = FakeRunner()
        code, text = self._run(["--inventory", str(INVENTORY), "--only", "nope"], runner)
        self.assertEqual(code, sweep.EXIT_CONFIG_ERROR)
        self.assertIn("nope", text)

    def test_workdir_defaults_to_a_temporary_directory(self) -> None:
        """Omitting ``--workdir`` must not write clones into the repo."""
        runner = FakeRunner(osv_results=[(0, json.dumps(CLEAN_REPORT))])
        with isolated_cwd() as tmp:
            code, _text = self._run(["--inventory", str(INVENTORY), "--only", "momentstudio"], runner)
            self.assertEqual(sorted(p.name for p in tmp.iterdir()), [])
        self.assertEqual(code, sweep.EXIT_OK)

    def test_min_severity_override_moves_the_t0_boundary(self) -> None:
        """The floor is configurable, and the body reflects the value used."""
        runner = FakeRunner(osv_results=[(1, json.dumps(VULNERABLE_REPORT))])
        with isolated_cwd() as tmp:
            _code, text = self._run(
                [
                    "--inventory",
                    str(INVENTORY),
                    "--only",
                    "momentstudio",
                    "--workdir",
                    str(tmp),
                    "--min-severity",
                    "2.0",
                    "--json",
                ],
                runner,
            )
        payload = json.loads(text)
        self.assertEqual(payload["repos"][0]["t0"], 1)
        self.assertEqual(payload["repos"][0]["t2"], 1)

    def test_run_url_flows_into_the_issue_body(self) -> None:
        """Provenance survives the CLI boundary."""
        runner = FakeRunner(osv_results=[(1, json.dumps(VULNERABLE_REPORT))])
        with isolated_cwd() as tmp:
            self._run(
                [
                    "--inventory",
                    str(INVENTORY),
                    "--only",
                    "momentstudio",
                    "--workdir",
                    str(tmp),
                    "--run-url",
                    "https://example.invalid/run/9",
                ],
                runner,
            )
        create = runner.argv_starting("gh", "issue", "create")[0]
        self.assertTrue(any("https://example.invalid/run/9" in item for item in create), create)

    def test_max_attempts_is_configurable(self) -> None:
        """A long upstream outage should not burn nine scans per repo."""
        runner = FakeRunner(osv_results=[(127, "")])
        with isolated_cwd() as tmp:
            code, _text = self._run(
                [
                    "--inventory",
                    str(INVENTORY),
                    "--only",
                    "momentstudio",
                    "--workdir",
                    str(tmp),
                    "--max-attempts",
                    "1",
                ],
                runner,
            )
        self.assertEqual(code, sweep.EXIT_INCOMPLETE)
        self.assertEqual(len(runner.argv_starting("osv-scanner")), 1)

    def test_default_clock_is_used_when_no_timestamp_is_injected(self) -> None:
        """Coverage cannot see inside a ternary, so exercise it explicitly.

        Every other test injects ``now`` for determinism, which would leave the
        production default (``datetime.now(UTC)``) unexecuted behind a green
        100% report - a coverage blind spot, not a covered path.
        """
        runner = FakeRunner(osv_results=[(1, json.dumps(VULNERABLE_REPORT))])
        before = dt.datetime.now(dt.UTC)
        out = io.StringIO()
        with isolated_cwd() as tmp, redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = sweep.main(
                ["--inventory", str(INVENTORY), "--only", "momentstudio", "--workdir", str(tmp), "--json"],
                runner=runner,
                sleeper=NoopSleeper(),
            )
        after = dt.datetime.now(dt.UTC)
        self.assertEqual(code, sweep.EXIT_OK)
        stamped = dt.datetime.fromisoformat(json.loads(out.getvalue())["generated_at"])
        self.assertLessEqual(before, stamped)
        self.assertLessEqual(stamped, after)


class IssueNumberParsingTests(unittest.TestCase):
    """The two number parsers, including the shapes coverage cannot branch on."""

    def test_integer_number_is_read(self) -> None:
        """The normal ``gh issue list`` shape."""
        self.assertEqual(sweep._issue_number({"number": 77}), 77)

    def test_non_integer_number_falls_back_to_zero(self) -> None:
        """A malformed record must not crash a whole-fleet sweep."""
        self.assertEqual(sweep._issue_number({"number": "77"}), 0)
        self.assertEqual(sweep._issue_number({}), 0)

    def test_create_url_number_is_parsed(self) -> None:
        """``gh issue create`` prints the URL; the tail is the number."""
        self.assertEqual(sweep._issue_number_from_url("https://github.com/o/r/issues/91\n"), 91)

    def test_unparseable_create_output_is_zero(self) -> None:
        """A parse miss is reported as 0, never as a failure."""
        self.assertEqual(sweep._issue_number_from_url("no url"), 0)
        self.assertEqual(sweep._issue_number_from_url(""), 0)


# ---------------------------------------------------------------------------
# 7. Workflow contract — scheduled, safe, and NOT a gate.
# ---------------------------------------------------------------------------


class ScheduledCveScanWorkflowTests(unittest.TestCase):
    """Invariants on ``.github/workflows/scheduled-cve-scan.yml``."""

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the workflow once per class."""
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)

    def test_runs_on_a_cron_and_on_manual_dispatch(self) -> None:
        """The whole point is that it fires with no developer action."""
        on_block = self.doc[True]
        self.assertIn("schedule", on_block)
        self.assertIn("workflow_dispatch", on_block)
        self.assertTrue(str(on_block["schedule"][0]["cron"]).strip())

    def test_it_is_not_triggered_by_pull_request_or_push(self) -> None:
        """A standing surface must not become a per-PR gate by accident."""
        on_block = self.doc[True]
        for event in ("pull_request", "pull_request_target", "push", "merge_group"):
            self.assertNotIn(event, on_block)

    def test_dry_run_defaults_to_true_on_dispatch(self) -> None:
        """House convention: a manual run never fires ``gh`` by accident."""
        inputs = self.doc[True]["workflow_dispatch"]["inputs"]
        self.assertTrue(inputs["dry_run"]["default"])

    def test_concurrency_group_is_a_fixed_string(self) -> None:
        """Overlapping crons must merge, not race on the same issues."""
        self.assertEqual(self.doc["concurrency"]["group"], "scheduled-cve-scan")
        self.assertFalse(self.doc["concurrency"]["cancel-in-progress"])

    def test_permissions_are_narrow(self) -> None:
        """Top-level empty; the job gets contents:read + issues:write only."""
        self.assertEqual(self.doc.get("permissions"), {})
        perms = self.doc["jobs"]["sweep"]["permissions"]
        self.assertEqual(perms, {"contents": "read", "issues": "write"})

    def test_checkout_does_not_persist_credentials(self) -> None:
        """CodeQL safety, and the sweep clones other repos explicitly."""
        steps = self.doc["jobs"]["sweep"]["steps"]
        checkouts = [step for step in steps if str(step.get("uses", "")).startswith("actions/checkout")]
        self.assertEqual(len(checkouts), 1)
        self.assertIs(checkouts[0]["with"]["persist-credentials"], False)

    def test_osv_scanner_is_pinned_to_the_gate_version(self) -> None:
        """Same binary as gate 6 - a T0 prediction from another version is noise."""
        self.assertIn(f"osv-scanner/releases/download/{sweep.OSV_SCANNER_VERSION}/", self.text)

    def test_no_github_expression_is_spliced_into_a_run_block(self) -> None:
        """Every dispatch input arrives through ``env:`` (CWE-78)."""
        for step in self.doc["jobs"]["sweep"]["steps"]:
            run = step.get("run")
            if not run:
                continue
            with self.subTest(step=step.get("name")):
                self.assertNotIn("${{ inputs.", run)
                self.assertNotIn("${{ github.", run)
                self.assertNotIn("${{ secrets.", run)

    def test_it_invokes_the_swept_module_and_not_an_inline_reimplementation(self) -> None:
        """The tested code is the code that runs."""
        self.assertIn("scripts.quality.scheduled_cve_scan", self.text)
        self.assertNotIn("python - <<'PY'", self.text)

    def test_it_passes_the_inventory_rather_than_a_repo_list(self) -> None:
        """ "Read the fleet from inventory/repos.yml" - mechanically checked."""
        self.assertIn("inventory/repos.yml", self.text)

    def test_it_is_not_a_required_status_check_anywhere_in_the_fleet(self) -> None:
        """Observability, not a gate. Detector-controlled against a real context."""
        contexts = set()
        for path in sorted(GENERATED_RULESETS.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for rule in payload.get("rules", []):
                if rule.get("type") != "required_status_checks":
                    continue
                for check in rule.get("parameters", {}).get("required_status_checks", []):
                    contexts.add(str(check.get("context", "")))
        self.assertIn("codeql / CodeQL", contexts, "detector control: a known required context must be found")
        job_name = str(self.doc["jobs"]["sweep"].get("name") or "sweep")
        for context in contexts:
            self.assertNotIn(job_name, context)
            self.assertNotIn(str(self.doc["name"]), context)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
