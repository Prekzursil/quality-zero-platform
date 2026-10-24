"""Behavioural tests for the round-10 lean-gate fixes in ``reusable-quality.yml``.

Round 10 closes the silent passes the 2026-10-24 audit measured:

* gate 4 (SAST) ran only when the caller shipped ``.quality/opengrep`` and gate 6
  (deps) only when it shipped ``osv-scanner.toml``, so a repo that had written
  neither got no SAST and no dependency scan while ``quality`` stayed green;
* the unittest coverage path measured lines only, and missed a ``tests/``
  directory without ``__init__.py``;
* the JS/TS lane never measured nested projects when a root ``package.json``
  existed;
* the gate-3 tools were installed unpinned and the biome pin was read from
  ``main`` at run time, so a caller's SHA pin did not pin them.

Plus the new ``deps-severity-floor`` input (``t0`` | ``any``) and the per-gate
summary. Every behavioural test EXTRACTS the real step script and EXECUTES it
under bash with fake tools injected as shell FUNCTIONS (a function wins over
every PATH lookup, including Git for Windows' bash wrapper).
"""

from __future__ import absolute_import

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from tests.lean_gate_support import ROOT, WORKFLOW, find_bash, run_step_script, step_named, workflow_steps

PINS = ROOT / ".quality" / "tool-pins.yml"
CLASSIFIER = ROOT / "scripts" / "quality" / "osv_severity_gate.py"
PLANNER = ROOT / "scripts" / "quality" / "jsts_projects.py"

# python3 is not on PATH on every dev box (Windows ships `py` / `python`).
PYTHON = sys.executable.replace("\\", "/")
FAKE_PYTHON3 = f'python3() {{ "{PYTHON}" "$@"; }}\n'
NO_SLEEP = "sleep() { :; }\n"

# A production npm finding with CVSS 8.8 and a published fix: T0, blocks under t0.
T0_REPORT: Dict[str, object] = {
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
                }
            ],
        }
    ]
}

# A dev-only, low-severity finding: T2 inventory, never blocks under t0.
T2_REPORT: Dict[str, object] = {
    "results": [
        {
            "source": {"path": "/repo/package-lock.json", "type": "lockfile"},
            "packages": [
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
                }
            ],
        }
    ]
}


def tool_pins() -> Dict[str, str]:
    """The ``pins`` block of ``.quality/tool-pins.yml``."""
    return yaml.safe_load(PINS.read_text(encoding="utf-8"))["pins"]


class _StepHarness(unittest.TestCase):
    """A scratch git work tree plus a runner for one extracted step."""

    def setUp(self) -> None:
        """Create the work tree and a relative RUNNER_TEMP inside it."""
        self.bash = find_bash()
        if self.bash is None:  # pragma: no cover - environment guard
            self.skipTest("no real bash found; cannot execute the gate steps")
        self.workdir = Path(tempfile.mkdtemp(prefix="qzp-round10-"))
        self.addCleanup(shutil.rmtree, self.workdir, True)
        (self.workdir / "rt" / "qzp-lean-gate").mkdir(parents=True)
        self._git("init", "-q")

    def _git(self, *args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
            cwd=str(self.workdir),
            check=True,
            capture_output=True,
            text=True,
        )

    def write(self, relative: str, text: str = "") -> Path:
        """Create a file in the work tree and stage it so `git ls-files` sees it."""
        path = self.workdir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        self._git("add", "--", relative)
        return path

    def run_step(self, name: str, env: Dict[str, str], prelude: str = "") -> subprocess.CompletedProcess:
        """Execute step ``name`` with ``prelude`` shell functions defined first."""
        assert self.bash is not None
        script = str(step_named(name)["run"])
        overrides = {"RUNNER_TEMP": "./rt", "GITHUB_STEP_SUMMARY": "./summary.md"}
        overrides.update(env)
        return run_step_script(prelude + script, workdir=self.workdir, env_overrides=overrides, bash=self.bash)

    def calls(self, log: str) -> List[str]:
        """The recorded invocations of a fake tool, one per line."""
        path = self.workdir / log
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


class InputValidationTests(_StepHarness):
    """``deps-severity-floor`` is validated before anything else runs."""

    def test_validation_is_the_first_step(self) -> None:
        """A typo must fail before a single tool is installed."""
        self.assertEqual(workflow_steps()[0].get("name"), "Validate workflow inputs")

    def test_supported_values_pass(self) -> None:
        """``t0`` and ``any`` are the whole vocabulary."""
        for value in ("t0", "any"):
            with self.subTest(value=value):
                result = self.run_step("Validate workflow inputs", {"DEPS_SEVERITY_FLOOR": value})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"deps-severity-floor: {value}", result.stdout)

    def test_anything_else_fails_loudly(self) -> None:
        """No silent fall-back to ``t0`` for a typo or a blank."""
        for value in ("T0", "zero", "", "any "):
            with self.subTest(value=value):
                result = self.run_step("Validate workflow inputs", {"DEPS_SEVERITY_FLOOR": value})
                self.assertEqual(result.returncode, 1)
                self.assertIn("::error title=invalid deps-severity-floor::", result.stdout)

    def test_the_input_is_declared_with_a_t0_default(self) -> None:
        """Existing callers keep today's behaviour unless they opt in."""
        document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        declared = document[True]["workflow_call"]["inputs"]["deps-severity-floor"]
        self.assertEqual(declared["default"], "t0")
        self.assertEqual(declared["type"], "string")


class Gate6DepsTests(_StepHarness):
    """Gate 6 always runs; osv-scanner decides what is scannable; the toml is optional."""

    # Records every call; answers --format json with $FAKE_OSV_JSON / rc
    # $FAKE_OSV_JSON_RC, every other call with rc $FAKE_OSV_RC.
    FAKE_OSV = """osv-scanner() {
  echo "$*" >> osv-calls.txt
  case " $* " in
    *" --format json "*) cat "$FAKE_OSV_JSON"; return "${FAKE_OSV_JSON_RC:-1}" ;;
  esac
  return "${FAKE_OSV_RC:-0}"
}
"""

    def setUp(self) -> None:
        """Ship the real classifier where the step expects it."""
        super().setUp()
        shutil.copy(CLASSIFIER, self.workdir / "rt" / "qzp-lean-gate" / "osv_severity_gate.py")

    def _report(self, document: Dict[str, object]) -> str:
        path = self.workdir / "report.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return "./report.json"

    def _run(self, *, floor: str = "t0", config: bool = False, **fake: str):
        env = {"OSVCONFIG": "true" if config else "false", "DEPS_SEVERITY_FLOOR": floor}
        env.update(fake)
        return self.run_step("gate-deps osv-scanner", env, FAKE_PYTHON3 + NO_SLEEP + self.FAKE_OSV)

    def test_scan_and_install_are_unconditional(self) -> None:
        """The old guards were ``osv-scanner.toml exists``, then a glob list of sources."""
        self.assertNotIn("if", step_named("gate-deps osv-scanner"))
        self.assertNotIn("if", step_named("Install osv-scanner"))
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("steps.detect.outputs.osvsources", text)
        self.assertNotIn('echo "osvsources=', text)

    def test_a_source_only_osv_scanner_knows_is_scanned(self) -> None:
        """THE review finding: bun.lock / uv.lock / NuGet matched no glob, so they were never scanned."""
        self.write("bun.lock", "{}")
        result = self._run(FAKE_OSV_RC="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls("osv-calls.txt"), ["scan source --recursive ."])

    def test_nothing_to_scan_is_the_scanners_own_verdict(self) -> None:
        """Exit 128 (no package sources) passes, said out loud and reported to the summary."""
        result = self._run(FAKE_OSV_RC="128")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("osv-scanner exited 128 (no package sources found) - nothing to scan", result.stdout)
        self.assertEqual(self.calls("osv-calls.txt"), ["scan source --recursive ."])
        self.assertIn("result=nothing-to-scan", (self.workdir / "github-output.txt").read_text(encoding="utf-8"))

    def test_without_a_toml_the_scan_still_runs_without_config(self) -> None:
        """THE hole: a repo with no osv-scanner.toml used to get no dependency gate."""
        result = self._run(FAKE_OSV_RC="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS gate-deps: osv-scanner found 0 actionable CVEs.", result.stdout)
        self.assertEqual(self.calls("osv-calls.txt"), ["scan source --recursive ."])

    def test_with_a_toml_both_scans_pass_the_config(self) -> None:
        """The caller's ignores apply to the plain scan AND the JSON re-scan."""
        result = self._run(config=True, FAKE_OSV_RC="1", FAKE_OSV_JSON=self._report(T2_REPORT))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls("osv-calls.txt"),
            [
                "scan source --recursive --config=osv-scanner.toml .",
                "scan source --recursive --config=osv-scanner.toml --format json .",
            ],
        )

    def test_t0_demotes_inventory_findings(self) -> None:
        """Under t0 a dev-only low finding is printed, not blocking."""
        result = self._run(FAKE_OSV_RC="1", FAKE_OSV_JSON=self._report(T2_REPORT))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0 findings clear the T0 severity floor", result.stdout)

    def test_t0_blocks_a_fixable_high_production_finding(self) -> None:
        """Under t0 a production CVSS 8.8 with a fix blocks."""
        result = self._run(FAKE_OSV_RC="1", FAKE_OSV_JSON=self._report(T0_REPORT))
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL gate-deps: at least one PRODUCTION dependency", result.stderr)

    def test_any_blocks_even_inventory_findings(self) -> None:
        """Zero tolerance: the same T2-only report that passes t0 fails here."""
        result = self._run(floor="any", FAKE_OSV_RC="1", FAKE_OSV_JSON=self._report(T2_REPORT))
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL gate-deps (deps-severity-floor: any)", result.stderr)
        # The table is still printed so the reader sees what blocked.
        self.assertIn("brace-expansion", result.stdout)

    def test_any_still_fails_when_the_table_cannot_be_rendered(self) -> None:
        """The plain scan already proved findings exist; a failed re-scan cannot clear them."""
        result = self._run(
            floor="any",
            FAKE_OSV_RC="1",
            FAKE_OSV_JSON=self._report(T2_REPORT),
            FAKE_OSV_JSON_RC="127",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("findings table could not be printed", result.stderr)

    def test_any_passes_a_clean_tree(self) -> None:
        """Zero tolerance is not "always red"."""
        result = self._run(floor="any", FAKE_OSV_RC="0")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_scan_errors_still_exit_75_after_retries(self) -> None:
        """127 is retried, then reported as a scan error, never a verdict."""
        result = self._run(FAKE_OSV_RC="127")
        self.assertEqual(result.returncode, 75)
        self.assertEqual(len(self.calls("osv-calls.txt")), 3)
        self.assertNotIn("break-glass", result.stderr)


class Gate4SastTests(_StepHarness):
    """Gate 4 always runs; only the ruleset is chosen."""

    FAKE_OPENGREP = 'opengrep() { echo "$*" >> og-calls.txt; return "${FAKE_OG_RC:-0}"; }\n'

    def _run(self, caller_ruleset: bool, **extra: str):
        env = {"OPENGREP_PATHS": "", "OPENGREP_CALLER_RULESET": "true" if caller_ruleset else "false"}
        env.update(extra)
        return self.run_step("gate-sast opengrep", env, self.FAKE_OPENGREP)

    def test_step_and_install_are_unconditional(self) -> None:
        """The old guard was ``.quality/opengrep exists``."""
        self.assertNotIn("if", step_named("gate-sast opengrep"))
        self.assertNotIn("if", step_named("Install opengrep"))
        self.assertNotIn("if", step_named("Materialize the platform default SAST ruleset"))

    def test_caller_ruleset_wins(self) -> None:
        """A repo with its own rules is scanned with them."""
        result = self._run(caller_ruleset=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("og-calls.txt")), 1)
        self.assertIn("--config .quality/opengrep --error", self.calls("og-calls.txt")[0])

    def test_no_caller_ruleset_scans_with_the_platform_default(self) -> None:
        """THE hole: no ruleset used to mean no SAST at all."""
        result = self._run(caller_ruleset=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::notice title=gate-sast platform default ruleset::", result.stdout)
        self.assertIn("--config ./rt/qzp-lean-gate/opengrep-default --error", self.calls("og-calls.txt")[0])

    def test_findings_fail_the_gate(self) -> None:
        """The scanner's verdict is the step's verdict."""
        result = self._run(caller_ruleset=False, FAKE_OG_RC="1")
        self.assertEqual(result.returncode, 1)

    def test_scan_paths_are_split_into_arguments(self) -> None:
        """``opengrep-paths`` still arrives as separate argv entries."""
        result = self._run(caller_ruleset=True, OPENGREP_PATHS="src lib")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.calls("og-calls.txt")[0].endswith("--exclude build src lib"))


class Gate3PythonTests(_StepHarness):
    """The Python lane is pinned, measures branches, and discovers every suite."""

    # The fake python answers per discovery start dir: $FAKE_RC_ROOT for
    # `-s .`, $FAKE_RC_TESTS for `-s tests`; everything else succeeds.
    FAKES = """pip() { echo "pip $*" >> py-calls.txt; }
python() {
  echo "python $*" >> py-calls.txt
  case "$*" in
    *"discover -s tests"*) return "${FAKE_RC_TESTS:-0}" ;;
    *"discover -s ."*) return "${FAKE_RC_ROOT:-0}" ;;
  esac
  return 0
}
"""

    def _run(self, **env: str):
        overrides = {"PYCOVCFG": "true", "PYTESTS": "true"}
        overrides.update(env)
        return self.run_step("gate-tests-coverage python", overrides, self.FAKES)

    def test_unittest_path_measures_branches_and_combines(self) -> None:
        """Line-only coverage could pass 100% with untested branches."""
        self.write("pyproject.toml", "[tool.coverage.run]\nbranch = true\n")
        self.write("pkg/__init__.py")
        self.write("pkg/tests/__init__.py")
        self.write("pkg/tests/test_x.py")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls("py-calls.txt")
        self.assertIn(f"pip install coverage=={tool_pins()['coverage']}", calls)
        self.assertIn("python -m coverage run --branch --parallel-mode -m unittest discover -s .", calls)
        self.assertNotIn("discover -s tests", " ".join(calls))
        self.assertEqual(
            calls[-2:], ["python -m coverage combine", "python -m coverage report --fail-under=100 --show-missing"]
        )

    def test_both_the_root_and_a_non_package_tests_dir_are_discovered(self) -> None:
        """A root discover never enters tests/ without __init__.py; -s tests alone misses the rest."""
        self.write("pyproject.toml", "[tool.coverage.run]\nbranch = true\n")
        self.write("tests/test_x.py")
        self.write("pkg/__init__.py")
        self.write("pkg/test_y.py")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls("py-calls.txt")
        self.assertIn("python -m coverage run --branch --parallel-mode -m unittest discover -s .", calls)
        self.assertIn("python -m coverage run --branch --parallel-mode -m unittest discover -s tests", calls)

    def test_one_empty_pass_is_fine(self) -> None:
        """Exit 5 (no tests) from the root pass is not a failure when tests/ ran."""
        self.write("pyproject.toml", "[tool.coverage.run]\nbranch = true\n")
        self.write("tests/test_x.py")
        result = self._run(FAKE_RC_ROOT="5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unittest discover -s .: no tests there.", result.stdout)

    def test_no_tests_run_anywhere_fails(self) -> None:
        """Every pass exiting 5 means nothing was measured."""
        self.write("pyproject.toml", "[tool.coverage.run]\nbranch = true\n")
        self.write("tests/test_x.py")
        result = self._run(FAKE_RC_ROOT="5", FAKE_RC_TESTS="5")
        self.assertEqual(result.returncode, 1)
        self.assertIn("discovered and ran NONE of them", result.stderr)

    def test_a_failing_pass_fails_the_lane(self) -> None:
        """Any exit other than 0 or 5 is a test failure."""
        self.write("pyproject.toml", "[tool.coverage.run]\nbranch = true\n")
        self.write("tests/test_x.py")
        result = self._run(FAKE_RC_TESTS="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("the tests discovered from 'tests' failed (exit 1)", result.stderr)

    def test_no_test_surface_reports_its_skip(self) -> None:
        """The skip is announced to the summary through the step's result output."""
        result = self._run(PYCOVCFG="false", PYTESTS="false")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("result=no-test-surface", (self.workdir / "github-output.txt").read_text(encoding="utf-8"))

    def test_pytest_path_is_pinned(self) -> None:
        """pytest, pytest-cov and coverage are the pinned versions."""
        self.write("pyproject.toml", "[tool.pytest.ini_options]\naddopts = '-q'\n")
        self.write("tests/test_x.py")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        pins = tool_pins()
        self.assertIn(
            f"pip install pytest=={pins['pytest']} pytest-cov=={pins['pytest-cov']} coverage=={pins['coverage']}",
            self.calls("py-calls.txt"),
        )
        self.assertIn("python -m pytest --cov --cov-branch --cov-fail-under=100", self.calls("py-calls.txt"))


class Gate3JstsTests(_StepHarness):
    """Every JS/TS project is measured, not only the root."""

    # node -e "...require('./<dir>/package.json')..." decides the lane; the fake
    # reads the same package.json with grep so no real node is needed.
    FAKE_NODE = r"""node() {
  local code="$2" dir
  [[ "$code" =~ require\(\'([^\']+)/package\.json\'\) ]] || return 1
  dir="${BASH_REMATCH[1]}"
  case "$code" in
    *test:coverage*) grep -q '"test:coverage"' "$dir/package.json" ;;
    *) grep -Eq '"test": *"[^"]*(vitest|jest)[^"]*--coverage' "$dir/package.json" ;;
  esac
}
npm() { echo "npm $* in $PWD" >> npm-calls.txt; }
"""

    def _prelude(self) -> str:
        # python3 runs the REAL planner (plan AND member-evidence modes); the
        # coverage-number assertion is recorded instead.
        return (
            self.FAKE_NODE
            + "python3() {\n"
            + '  case "$1" in\n'
            + f'    *jsts_projects.py) shift; "{PYTHON}" "$PLANNER" "$@" ;;\n'
            + '    *) echo "assert $*" >> assert-calls.txt ;;\n'
            + "  esac\n"
            + "}\n"
        )

    def _run(self):
        env = {"JSTS_COVERAGE_MIN": "100", "PLANNER": str(PLANNER).replace("\\", "/")}
        return self.run_step("gate-tests-coverage jsts", env, self._prelude())

    def _measured_dirs(self) -> List[str]:
        dirs = []
        for line in self.calls("assert-calls.txt"):
            match = re.search(r"--project-dir (\S+)", line)
            if match:
                dirs.append(match.group(1))
        return dirs

    def test_root_and_nested_projects_are_all_measured(self) -> None:
        """THE hole: a root package.json used to hide every nested project."""
        self.write("package.json", '{"scripts": {"test:coverage": "vitest run --coverage"}}')
        self.write("src/a.test.ts")
        self.write("frontend/package.json", '{"scripts": {"test": "jest --coverage"}}')
        self.write("frontend/src/b.spec.tsx")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._measured_dirs(), [".", "frontend"])
        self.assertIn("measured coverage in 2 project dir(s)", result.stdout)

    def test_nested_project_with_tests_but_no_lane_fails(self) -> None:
        """Unmeasured tests are named, not passed over."""
        self.write("package.json", '{"scripts": {"test:coverage": "vitest run --coverage"}}')
        self.write("src/a.test.ts")
        self.write("admin/package.json", '{"scripts": {"test": "vitest"}}')
        self.write("admin/x.test.ts")
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("never measured: admin", result.stderr)

    def test_a_lane_less_workspace_member_fails_even_when_the_root_runs(self) -> None:
        """THE review findings: a root workspace run (or a report on disk) is never a member's lane.

        A root `jest src --coverage` that imports the member covers its code
        without running a single member test, and a committed report can name
        the member's files without any run at all.
        """
        self.write(
            "package.json",
            '{"workspaces": ["packages/*"], "scripts": {"test:coverage": "jest src --coverage"}}',
        )
        self.write("src/a.test.ts")
        self.write("packages/ui/package.json", '{"name": "ui"}')
        self.write("packages/ui/button.test.ts")
        (self.workdir / "coverage").mkdir()
        (self.workdir / "coverage" / "lcov.info").write_text("SF:packages/ui/button.ts\n", encoding="utf-8")
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self._measured_dirs(), ["."])
        self.assertIn("never measured: packages/ui (workspace member)", result.stderr)
        self.assertIn("a root workspace run does not count for a member", result.stderr)

    def test_stale_coverage_reports_are_cleared_before_a_run(self) -> None:
        """The number assertion reads <dir>/coverage, so a committed report must be gone first."""
        self.write("package.json", '{"scripts": {"test:coverage": "vitest run --coverage"}}')
        self.write("src/a.test.ts")
        self.write("coverage/coverage-summary.json", '{"total": {"lines": {"pct": 100}}}')
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.workdir / "coverage").exists())

    def test_mocha_style_test_directories_are_a_test_surface(self) -> None:
        """THE review finding: `test/smoke.js` used to read as "no tests", skipping the lane."""
        self.write("package.json", '{"scripts": {"test:coverage": "c8 mocha"}}')
        self.write("lib/index.js")
        self.write("test/smoke.js")
        self.write("api/package.json", '{"scripts": {"test:coverage": "c8 mocha"}}')
        self.write("api/tests/unit.ts")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._measured_dirs(), [".", "api"])

    def test_an_aggregator_root_is_not_required_when_members_measure_themselves(self) -> None:
        """THE review finding: a root with no tests and no lane used to be flagged unmeasured."""
        self.write("package.json", '{"private": true, "workspaces": ["packages/*"]}')
        for name in ("api", "ui"):
            self.write(f"packages/{name}/package.json", '{"scripts": {"test:coverage": "vitest run --coverage"}}')
            self.write(f"packages/{name}/a.test.ts")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._measured_dirs(), ["packages/api", "packages/ui"])
        self.assertIn(
            "project .: no coverage lane and no JS/TS test files of its own - nothing to measure.", result.stdout
        )

    def test_a_declared_root_lane_runs_even_without_root_test_files(self) -> None:
        """A declared lane always runs (a workspace runner may own every test); members run theirs too."""
        self.write(
            "package.json",
            '{"workspaces": ["packages/*"], "scripts": {"test:coverage": "vitest run --coverage"}}',
        )
        self.write("packages/ui/package.json", '{"scripts": {"test:coverage": "vitest run --coverage"}}')
        self.write("packages/ui/a.test.ts")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._measured_dirs(), [".", "packages/ui"])

    def test_a_cypress_only_project_with_a_lane_runs(self) -> None:
        """THE review finding: `cypress/e2e/login.cy.ts` must not read as "no tests"."""
        self.write("web/package.json", '{"scripts": {"test:coverage": "cypress run --env coverage=true"}}')
        self.write("web/cypress/e2e/login.cy.ts")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._measured_dirs(), ["web"])

    def test_fixture_package_manifests_are_test_data_not_projects(self) -> None:
        """THE review finding: tests/fixtures/sample/package.json is an input, not a lane-less project."""
        self.write("package.json", '{"scripts": {"test:coverage": "vitest run --coverage"}}')
        self.write("src/a.test.ts")
        self.write("tests/fixtures/sample/package.json", '{"name": "sample"}')
        self.write("tests/fixtures/sample/index.js")
        self.write("test/__mocks__/fs.js")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._measured_dirs(), ["."])

    def test_a_lane_less_member_without_a_root_lane_is_unmeasured(self) -> None:
        """A member with tests and no lane of its own fails by name."""
        self.write("package.json", '{"workspaces": ["packages/*"]}')
        self.write("packages/ui/package.json", '{"name": "ui"}')
        self.write("packages/ui/a.test.ts")
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("never measured: packages/ui", result.stderr)

    def test_a_declared_lane_runs_without_recognised_test_files(self) -> None:
        """No file-name heuristic decides that a declared runner has nothing to run."""
        self.write("package.json", '{"scripts": {"test:coverage": "vitest run --coverage"}}')
        self.write("src/a.test.ts")
        self.write("tools/package.json", '{"scripts": {"test:coverage": "node --test --experimental-test-coverage"}}')
        self.write("tools/build.mjs")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._measured_dirs(), [".", "tools"])

    def test_a_lane_less_project_without_tests_is_nothing_to_measure(self) -> None:
        """A tooling package with neither a lane nor tests is reported, not failed."""
        self.write("package.json", '{"scripts": {"test:coverage": "vitest run --coverage"}}')
        self.write("src/a.test.ts")
        self.write("tools/package.json", '{"name": "tools"}')
        self.write("tools/build.mjs")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._measured_dirs(), ["."])
        self.assertIn("project tools: no coverage lane and no JS/TS test files of its own", result.stdout)

    def test_orphan_tests_fail_even_when_another_project_passes(self) -> None:
        """A test outside every project is run by no coverage lane."""
        self.write("frontend/package.json", '{"scripts": {"test:coverage": "vitest run --coverage"}}')
        self.write("frontend/src/app.test.ts")
        self.write("e2e/flow.spec.ts")
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("outside every package.json project", result.stderr)
        self.assertIn("e2e/flow.spec.ts", result.stderr)

    def test_no_test_surface_is_still_a_documented_skip(self) -> None:
        """Source with zero test files keeps its narrow skip, reported to the summary."""
        self.write("package.json", '{"name": "lib"}')
        self.write("index.js")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no test surface by design", result.stdout)
        self.assertIn("result=no-test-surface", (self.workdir / "github-output.txt").read_text(encoding="utf-8"))

    def test_non_ascii_paths_still_match_their_project(self) -> None:
        """`git ls-files -z` keeps non-ASCII names unquoted."""
        self.write("cafés/package.json", '{"scripts": {"test:coverage": "vitest run --coverage"}}')
        self.write("cafés/über.test.ts")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._measured_dirs(), ["cafés"])


class SummaryStepTests(_StepHarness):
    """The per-gate summary covers every gate and tells skip from not-run."""

    OUTCOME_ENV = {
        "LINT_JSTS": "skipped",
        "LINT_PY": "success",
        "TYPES_TSC": "skipped",
        "TYPES_PY": "success",
        "COV_PY": "failure",
        "COV_JSTS": "skipped",
        "COV_COMPILED": "skipped",
        "SAST": "skipped",
        "SECRETS": "skipped",
        "DEPS": "skipped",
        "CHARTER_CHECK": "skipped",
        # Actions sets an env var from a missing step output to "" (never unset).
        "COV_PY_RESULT": "",
        "COV_JSTS_RESULT": "",
        "COV_COMPILED_RESULT": "",
        "DEPS_RESULT": "",
    }

    def test_every_gate_step_is_reported(self) -> None:
        """A gate added later without a summary row would be invisible."""
        summary_env = step_named("Lean-gate summary")["env"]
        referenced = set(re.findall(r"steps\.(\w+)\.outcome", " ".join(str(v) for v in summary_env.values())))
        for step in workflow_steps():
            name = str(step.get("name", ""))
            if name.startswith("gate-") or name == "charter-check":
                with self.subTest(step=name):
                    self.assertIn("id", step, name)
                    self.assertIn(step["id"], referenced, name)

    def test_summary_always_runs(self) -> None:
        """A red run is exactly when the table matters."""
        self.assertEqual(step_named("Lean-gate summary").get("if"), "always()")

    def test_rows_distinguish_skipped_from_not_run(self) -> None:
        """A Python repo whose coverage failed: later gates NOT RUN, JS skipped."""
        env = dict(self.OUTCOME_ENV)
        env.update({"DETECT_OUTCOME": "success", "PY": "true", "TS": "false", "JSTS": "false"})
        env.update({"GO": "false", "CPP": "false", "CHARTER": "false"})
        result = self.run_step("Lean-gate summary", env)
        self.assertEqual(result.returncode, 0, result.stderr)
        table = (self.workdir / "summary.md").read_text(encoding="utf-8")
        self.assertIn("| 3 tests+coverage | Python | ran - **FAILED** |", table)
        self.assertIn("| 1 lint+format | oxlint + biome (JS/TS) | skipped - no JS/TS source |", table)
        self.assertIn("| 4 SAST | opengrep | NOT RUN - an earlier step failed |", table)
        self.assertIn("| charter | charter-check | skipped - no .quality/charter.yml |", table)

    def test_rows_when_detection_never_completed(self) -> None:
        """An input-validation failure leaves every gate NOT RUN, never skipped."""
        env = dict.fromkeys(self.OUTCOME_ENV, "")
        env.update({"DETECT_OUTCOME": "skipped", "PY": "", "TS": "", "JSTS": "", "GO": "", "CPP": "", "CHARTER": ""})
        env["LINT_PY"] = "cancelled"
        result = self.run_step("Lean-gate summary", env)
        self.assertEqual(result.returncode, 0, result.stderr)
        table = (self.workdir / "summary.md").read_text(encoding="utf-8")
        self.assertIn("| 1 lint+format | ruff (Python) | cancelled |", table)
        self.assertIn("| 2 types | tsc | NOT RUN - language detection did not complete |", table)
        self.assertIn("| 3 tests+coverage | Go / C++ | NOT RUN - language detection did not complete |", table)

    def test_internal_skips_are_reported_as_skips(self) -> None:
        """A step that passed by skipping measurement is not a measured pass."""
        env = dict(self.OUTCOME_ENV)
        env.update({"DETECT_OUTCOME": "success", "PY": "true", "TS": "false", "JSTS": "false"})
        env.update({"GO": "false", "CPP": "false", "CHARTER": "false", "COV_PY": "success", "DEPS": "success"})
        env.update({"COV_PY_RESULT": "no-test-surface", "DEPS_RESULT": "nothing-to-scan"})
        result = self.run_step("Lean-gate summary", env)
        self.assertEqual(result.returncode, 0, result.stderr)
        table = (self.workdir / "summary.md").read_text(encoding="utf-8")
        self.assertIn(
            "| 3 tests+coverage | Python | skipped - source present but zero test files (no test surface) |", table
        )
        self.assertIn("| 6 deps | osv-scanner | ran - nothing to scan (osv-scanner found no package sources) |", table)

    def test_compiled_row_applies_to_go_or_cpp(self) -> None:
        """Either compiled language makes the compiled lane applicable."""
        env = dict(self.OUTCOME_ENV)
        env.update({"DETECT_OUTCOME": "success", "PY": "false", "TS": "false", "JSTS": "false"})
        env.update({"GO": "false", "CPP": "true", "CHARTER": "false", "COV_PY": "skipped"})
        result = self.run_step("Lean-gate summary", env)
        self.assertEqual(result.returncode, 0, result.stderr)
        table = (self.workdir / "summary.md").read_text(encoding="utf-8")
        self.assertIn("| 3 tests+coverage | Go / C++ | NOT RUN - an earlier step failed |", table)


class PinnedLiteralTests(unittest.TestCase):
    """A caller's SHA pin must pin every tool version the gate runs."""

    def setUp(self) -> None:
        """Read the workflow once."""
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_no_tool_version_is_read_from_main_at_run_time(self) -> None:
        """round-9's runtime fetch of tool-pins.yml from `main` is gone."""
        executed = "\n".join(line for line in self.text.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("raw.githubusercontent.com", executed)

    def test_biome_literal_equals_the_pin_file(self) -> None:
        """fleet-health greps exactly this shape."""
        self.assertIn(f'biome_pin="{tool_pins()["biome"]}"', self.text)

    def test_gate3_python_tools_equal_the_pin_file(self) -> None:
        """No unpinned pip install remains in the Python lane."""
        pins = tool_pins()
        script = str(step_named("gate-tests-coverage python")["run"])
        self.assertIn(
            f"pip install pytest=={pins['pytest']} pytest-cov=={pins['pytest-cov']} coverage=={pins['coverage']}",
            script,
        )
        self.assertIn(f"pip install coverage=={pins['coverage']}", script)
        self.assertNotRegex(script, r"pip install (pytest|pytest-cov|coverage)(\s|$)")

    def test_node_default_is_a_supported_lts(self) -> None:
        """Node 20 reached end of life on 2026-04-30."""
        document = yaml.safe_load(self.text)
        self.assertEqual(document[True]["workflow_call"]["inputs"]["node-version"]["default"], "24")


class FleetHealthPinDriftTests(unittest.TestCase):
    """fleet-health's weekly pin-drift step agrees with this ref."""

    def test_pin_drift_step_reports_no_drift(self) -> None:
        """Run the real step against the real pin file and workflow."""
        bash = find_bash()
        if bash is None:  # pragma: no cover - environment guard
            self.skipTest("no real bash found")
        document = yaml.safe_load((ROOT / ".github" / "workflows" / "fleet-health.yml").read_text(encoding="utf-8"))
        step = next(s for s in document["jobs"]["sweep"]["steps"] if s.get("id") == "pin_drift")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out.txt"
            result = subprocess.run(
                [bash, "-c", str(step["run"])],
                cwd=str(ROOT),
                env={**_environ(), "GITHUB_OUTPUT": str(output)},
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pin drift: none (all 10 pins and 3 asset digests", result.stdout)


def _environ(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The current environment, optionally extended."""
    import os

    env = dict(os.environ)
    env.update(extra or {})
    return env


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
