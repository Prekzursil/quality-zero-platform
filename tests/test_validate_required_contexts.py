"""Coverage for ``scripts/quality/validate_required_contexts.py``.

The gh layer is exercised through an injectable scripted runner (no real
``gh`` binary is invoked); profile/inventory fixtures are written into a
temp workspace so the pure logic is tested against real YAML shapes.
"""

from __future__ import absolute_import

import io
import json
import subprocess
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, List, Sequence
from unittest.mock import MagicMock

from scripts.quality import validate_required_contexts as vrc


class ScriptedRunner:
    """subprocess.run fake dispatching on the leading gh subcommand."""

    def __init__(self, *, pages: Any = None, returncode: int = 0, stderr: str = "", stdout: str = "") -> None:
        """Configure the response every ``gh api`` call receives."""
        self.pages = pages
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout
        self.calls: List[Sequence[str]] = []

    def __call__(self, args: Sequence[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Record the call and answer with the configured response."""
        self.calls.append(list(args))
        stdout = self.stdout
        if not stdout and self.pages is not None:
            stdout = json.dumps(self.pages)
        return subprocess.CompletedProcess(
            args=list(args), returncode=self.returncode, stdout=stdout, stderr=self.stderr
        )


def _check_run(name: str) -> dict:
    """Build one minimal check-run record."""
    return {"name": name, "status": "completed", "conclusion": "success"}


class LoadInventoryEntriesTests(unittest.TestCase):
    """Inventory parsing and its failure modes."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _write(self, text: str) -> Path:
        path = self.dir / "repos.yml"
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return path

    def test_reads_slug_profile_and_default_branch(self) -> None:
        rows, errors = vrc.load_inventory_entries(
            self._write(
                """
                version: 1
                repos:
                  - slug: Prekzursil/a
                    profile: a
                    rollout: observe
                    default_branch: trunk
                  - slug: Prekzursil/b
                    profile: b
                """
            )
        )
        self.assertEqual(errors, [])
        self.assertEqual(rows[0], {"slug": "Prekzursil/a", "profile": "a", "default_branch": "trunk"})
        self.assertEqual(rows[1]["default_branch"], "main")

    def test_missing_repos_list_is_a_roster_error(self) -> None:
        rows, errors = vrc.load_inventory_entries(self._write("version: 1\n"))
        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("no `repos` list", errors[0])

    def test_non_mapping_yaml_is_a_roster_error(self) -> None:
        path = self.dir / "repos.yml"
        path.write_text("- just\n- a\n- list\n", encoding="utf-8")
        rows, errors = vrc.load_inventory_entries(path)
        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 1)

    def test_bad_rows_are_skipped_with_errors(self) -> None:
        rows, errors = vrc.load_inventory_entries(
            self._write(
                """
                version: 1
                repos:
                  - 17
                  - notes: row without slug
                  - slug: "  "
                  - slug: Prekzursil/ok
                    profile: ok
                """
            )
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(errors), 3)


class LoadProfileTests(unittest.TestCase):
    """Profile loading failure modes."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_missing_profile_name(self) -> None:
        profile, error = vrc.load_profile(self.dir, "")
        self.assertIsNone(profile)
        self.assertIn("no `profile` name", error)

    def test_missing_file(self) -> None:
        profile, error = vrc.load_profile(self.dir, "ghost")
        self.assertIsNone(profile)
        self.assertIn("profile file not found", error)

    def test_non_mapping_profile(self) -> None:
        (self.dir / "weird.yml").write_text("- a\n- b\n", encoding="utf-8")
        profile, error = vrc.load_profile(self.dir, "weird")
        self.assertIsNone(profile)
        self.assertIn("not a mapping", error)

    def test_valid_profile_loads(self) -> None:
        (self.dir / "ok.yml").write_text("slug: x/y\nversion: 2\n", encoding="utf-8")
        profile, error = vrc.load_profile(self.dir, "ok")
        self.assertEqual(error, "")
        self.assertEqual(profile["slug"], "x/y")


class CollectRequiredTests(unittest.TestCase):
    """required_contexts selection: target wins, always is the fallback."""

    def test_no_section(self) -> None:
        names, error = vrc.collect_required({"slug": "x/y"})
        self.assertEqual(names, [])
        self.assertIn("no `required_contexts`", error)

    def test_target_wins_over_always(self) -> None:
        names, error = vrc.collect_required({"required_contexts": {"always": ["a"], "target": ["b", "c"]}})
        self.assertEqual(error, "")
        self.assertEqual(names, ["b", "c"])

    def test_always_used_when_target_missing_or_empty(self) -> None:
        self.assertEqual(vrc.collect_required({"required_contexts": {"always": ["a"]}})[0], ["a"])
        self.assertEqual(vrc.collect_required({"required_contexts": {"always": ["a"], "target": []}})[0], ["a"])

    def test_non_list_target_and_always(self) -> None:
        _names, error = vrc.collect_required({"required_contexts": {"target": "nope", "always": "nope"}})
        self.assertIn("not a list", error)

    def test_blank_names_dropped_and_empty_is_error(self) -> None:
        names, _error = vrc.collect_required({"required_contexts": {"target": ["a", "  ", ""]}})
        self.assertEqual(names, ["a"])
        names, error = vrc.collect_required({"required_contexts": {"target": ["  "]}})
        self.assertEqual(names, [])
        self.assertIn("empty", error)


class ResolveContextTests(unittest.TestCase):
    """Exact / unique-suffix resolution (mirrors check_required_checks)."""

    def test_exact_match(self) -> None:
        self.assertTrue(vrc.resolve_context("quality / quality", ["quality / quality"]))

    def test_unique_suffix_match(self) -> None:
        self.assertTrue(vrc.resolve_context("quality", ["quality / quality"]))

    def test_ambiguous_suffix_does_not_resolve(self) -> None:
        self.assertFalse(vrc.resolve_context("quality", ["a / quality", "b / quality"]))

    def test_no_match(self) -> None:
        self.assertFalse(vrc.resolve_context("quality", ["test (ubuntu-latest, 3.12)"]))


class ClassifyTests(unittest.TestCase):
    """matched / phantom / PR-only triage."""

    def test_all_three_buckets(self) -> None:
        matched, phantoms, pr_only = vrc.classify(
            ["quality / quality", "codeql / CodeQL", "CodeQL"],
            ["quality / quality"],
        )
        self.assertEqual(matched, ["quality / quality"])
        self.assertEqual(phantoms, ["codeql / CodeQL"])
        self.assertEqual(pr_only, ["CodeQL"])


class FetchMeasuredContextsTests(unittest.TestCase):
    """The gh api wrapper: success, failure, and payload shapes."""

    def test_success_dedupes_and_skips_blank_names(self) -> None:
        runner = ScriptedRunner(pages=[[{"check_runs": [_check_run("a"), _check_run(""), _check_run("a")]}]])
        names, error = vrc.fetch_measured_contexts("x/y", "main", runner=runner)
        self.assertEqual(error, "")
        self.assertEqual(names, ["a"])
        self.assertIn("repos/x/y/commits/main/check-runs", " ".join(runner.calls[0]))

    def test_single_dict_payload_shape(self) -> None:
        runner = ScriptedRunner(pages={"check_runs": [_check_run("a")]})
        names, _error = vrc.fetch_measured_contexts("x/y", "main", runner=runner)
        self.assertEqual(names, ["a"])

    def test_slurp_junk_items_are_ignored(self) -> None:
        runner = ScriptedRunner(pages=[[{"check_runs": [_check_run("a")]}], "junk-string", 17])
        names, error = vrc.fetch_measured_contexts("x/y", "main", runner=runner)
        self.assertEqual(error, "")
        self.assertEqual(names, ["a"])

    def test_gh_failure_reports_last_stderr_line(self) -> None:
        runner = ScriptedRunner(returncode=1, stderr="first\ngh: Not Found\n")
        names, error = vrc.fetch_measured_contexts("x/y", "main", runner=runner)
        self.assertIsNone(names)
        self.assertIn("gh api failed: gh: Not Found", error)

    def test_gh_failure_with_no_stderr_reports_exit_code(self) -> None:
        runner = ScriptedRunner(returncode=1, stderr="")
        names, error = vrc.fetch_measured_contexts("x/y", "main", runner=runner)
        self.assertIsNone(names)
        self.assertIn("exit 1", error)

    def test_unparseable_json(self) -> None:
        runner = ScriptedRunner(stdout="{not json")
        names, error = vrc.fetch_measured_contexts("x/y", "main", runner=runner)
        self.assertIsNone(names)
        self.assertIn("unparseable JSON", error)


class RepoVerdictTests(unittest.TestCase):
    """Verdict + report status helpers."""

    def test_status_precedence(self) -> None:
        base = {"slug": "x/y", "branch": "main"}
        self.assertEqual(vrc.RepoVerdict(**base, error="boom").status, "error")
        self.assertEqual(vrc.RepoVerdict(**base, phantoms=("a",)).status, "phantom")
        self.assertEqual(vrc.RepoVerdict(**base, pr_only_unverified=("CodeQL",)).status, "pr_only")
        self.assertEqual(vrc.RepoVerdict(**base).status, "clean")

    def test_fleet_report_status_and_exit_codes(self) -> None:
        base = {"slug": "x/y", "branch": "main"}
        clean = vrc.RepoVerdict(**base)
        phantom = vrc.RepoVerdict(**base, phantoms=("a",))
        pr_only = vrc.RepoVerdict(**base, pr_only_unverified=("CodeQL",))
        errored = vrc.RepoVerdict(**base, error="unmeasurable")
        self.assertEqual(vrc.FleetReport().status, "clean")
        self.assertEqual(vrc.FleetReport().exit_code(), vrc.EXIT_OK)
        self.assertEqual(vrc.FleetReport(verdicts=(clean, pr_only)).status, "pr_only")
        self.assertEqual(vrc.FleetReport(verdicts=(clean, pr_only)).exit_code(), vrc.EXIT_OK)
        self.assertEqual(vrc.FleetReport(verdicts=(clean, phantom)).exit_code(), vrc.EXIT_PHANTOM)
        self.assertEqual(vrc.FleetReport(verdicts=(clean, errored)).exit_code(), vrc.EXIT_PHANTOM)
        self.assertEqual(vrc.FleetReport(roster_errors=("bad",)).status, "roster_error")
        self.assertEqual(vrc.FleetReport(roster_errors=("bad",)).exit_code(), vrc.EXIT_ROSTER)


class BuildReportTests(unittest.TestCase):
    """End-to-end pure pipeline: entries -> verdicts."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "good.yml").write_text(
            "slug: x/good\nrequired_contexts:\n  target:\n    - quality / quality\n    - CodeQL\n",
            encoding="utf-8",
        )

    def test_clean_and_pr_only_repo(self) -> None:
        runner = ScriptedRunner(pages=[{"check_runs": [_check_run("quality / quality")]}])
        report = vrc.build_report(
            [{"slug": "x/good", "profile": "good", "default_branch": "main"}],
            self.dir,
            runner=runner,
        )
        self.assertEqual(report.status, "pr_only")
        self.assertEqual(report.verdicts[0].matched, ("quality / quality",))
        self.assertEqual(report.verdicts[0].pr_only_unverified, ("CodeQL",))

    def test_profile_error_becomes_verdict_error_not_roster_error(self) -> None:
        runner = ScriptedRunner()
        report = vrc.build_report(
            [{"slug": "x/ghost", "profile": "ghost", "default_branch": "main"}],
            self.dir,
            runner=runner,
        )
        self.assertEqual(report.roster_errors, ())
        self.assertEqual(report.verdicts[0].status, "error")

    def test_unmeasurable_repo_is_an_error_verdict(self) -> None:
        runner = ScriptedRunner(returncode=1, stderr="gh: Not Found")
        report = vrc.build_report(
            [{"slug": "x/good", "profile": "good", "default_branch": "main"}],
            self.dir,
            runner=runner,
        )
        self.assertEqual(report.verdicts[0].status, "error")
        self.assertIn("unmeasurable", report.verdicts[0].error)
        self.assertEqual(report.exit_code(), vrc.EXIT_PHANTOM)


class RenderTests(unittest.TestCase):
    """JSON + text rendering for every verdict status."""

    def _report(self) -> vrc.FleetReport:
        base = {"slug": "x/y", "branch": "main"}
        return vrc.FleetReport(
            verdicts=(
                vrc.RepoVerdict(**base),
                vrc.RepoVerdict(**base, phantoms=("codeql / CodeQL",)),
                vrc.RepoVerdict(**base, pr_only_unverified=("CodeQL",)),
                vrc.RepoVerdict(**base, error="unmeasurable: gh api failed"),
            ),
            roster_errors=("inventory: no `repos` list could be read",),
        )

    def test_json_shape(self) -> None:
        payload = vrc.report_json(self._report())
        self.assertEqual(payload["status"], "roster_error")
        self.assertEqual(payload["repos"][1]["phantom_contexts"], ["codeql / CodeQL"])
        self.assertEqual(payload["repos"][2]["pr_only_unverified_on_main"], ["CodeQL"])
        self.assertEqual(payload["repos"][3]["status"], "error")

    def test_text_renders_every_marker(self) -> None:
        text = vrc.render_text(self._report())
        for marker in ("PHANTOM", "pr-only", "ERROR", "roster errors", "required but NOT emitted"):
            self.assertIn(marker, text)
        self.assertIn("PR-only, needs a PR-head measurement", text)


class MainTests(unittest.TestCase):
    """CLI wiring: filters, report writing, exit codes."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "repos.yml").write_text(
            "version: 1\nrepos:\n"
            "  - slug: Prekzursil/fractal-darkroom\n"
            "    profile: fractal-darkroom\n"
            "    default_branch: main\n",
            encoding="utf-8",
        )
        profiles = self.dir / "profiles" / "repos"
        profiles.mkdir(parents=True)
        (profiles / "fractal-darkroom.yml").write_text(
            "slug: Prekzursil/fractal-darkroom\nrequired_contexts:\n  target:\n    - quality / quality\n    - CodeQL\n",
            encoding="utf-8",
        )

    def test_missing_inventory_exits_roster(self) -> None:
        self.assertEqual(vrc.main(["--inventory", str(self.dir / "nope.yml")]), vrc.EXIT_ROSTER)

    def test_repo_filter_without_match_exits_roster(self) -> None:
        self.assertEqual(
            vrc.main(["--inventory", str(self.dir / "repos.yml"), "--repo", "x/ghost"]),
            vrc.EXIT_ROSTER,
        )

    def test_pr_only_repo_exits_zero_and_writes_json(self) -> None:
        out_json = self.dir / "report.json"
        code = vrc.main(
            [
                "--inventory",
                str(self.dir / "repos.yml"),
                "--profiles-dir",
                str(self.dir / "profiles" / "repos"),
                "--repo",
                "Prekzursil/fractal-darkroom",
                "--out-json",
                str(out_json),
            ],
            runner=ScriptedRunner(pages=[{"check_runs": [_check_run("quality / quality")]}]),
        )
        self.assertEqual(code, vrc.EXIT_OK)
        payload = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertEqual(payload["repos"][0]["pr_only_unverified_on_main"], ["CodeQL"])

    def test_phantom_repo_exits_one(self) -> None:
        code = vrc.main(
            [
                "--inventory",
                str(self.dir / "repos.yml"),
                "--profiles-dir",
                str(self.dir / "profiles" / "repos"),
            ],
            runner=ScriptedRunner(pages=[{"check_runs": [_check_run("something else")]}]),
        )
        self.assertEqual(code, vrc.EXIT_PHANTOM)

    def test_missing_branch_json_write_dir_must_exist(self) -> None:
        # A verdict-level error (unmeasurable repo) also exits PHANTOM via CLI.
        code = vrc.main(
            [
                "--inventory",
                str(self.dir / "repos.yml"),
                "--profiles-dir",
                str(self.dir / "profiles" / "repos"),
            ],
            runner=ScriptedRunner(returncode=1, stderr="gh: Not Found"),
        )
        self.assertEqual(code, vrc.EXIT_PHANTOM)


class OutJsonDirectoryTests(unittest.TestCase):
    """fleet-health passes build/fleet-health-contexts.json; nothing else creates build/."""

    def test_the_report_directory_is_created(self) -> None:
        """The write crashed (exit 1, read as 'findings') when the directory did not exist."""
        with tempfile.TemporaryDirectory() as tmp:
            inventory = Path(tmp) / "repos.yml"
            inventory.write_text("version: 1\nrepos: []\n", encoding="utf-8")
            out = Path(tmp) / "build" / "nested" / "contexts.json"
            with redirect_stdout(io.StringIO()):
                code = vrc.main(["--inventory", str(inventory), "--out-json", str(out)], runner=MagicMock())
            self.assertIn(code, (0, 1))
            self.assertTrue(out.is_file())
            self.assertIn("repos", json.loads(out.read_text(encoding="utf-8")))
