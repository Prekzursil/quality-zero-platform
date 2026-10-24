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
    """Gate 6 runs whenever there is something to scan; the toml is optional."""

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

    def _run(self, *, floor: str = "t0", config: bool = False, sources: bool = True, **fake: str):
        env = {
            "OSVSOURCES": "true" if sources else "false",
            "OSVCONFIG": "true" if config else "false",
            "DEPS_SEVERITY_FLOOR": floor,
        }
        env.update(fake)
        return self.run_step("gate-deps osv-scanner", env, FAKE_PYTHON3 + NO_SLEEP + self.FAKE_OSV)

    def test_step_is_unconditional(self) -> None:
        """The old guard was ``osv-scanner.toml exists``: the inverted trigger."""
        self.assertNotIn("if", step_named("gate-deps osv-scanner"))
        install = step_named("Install osv-scanner")
        self.assertEqual(install.get("if"), "steps.detect.outputs.osvsources == 'true'")

    def test_nothing_to_scan_passes_without_running_the_scanner(self) -> None:
        """No manifests at all is a pass, said out loud."""
        result = self._run(sources=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing for osv-scanner to scan", result.stdout)
        self.assertEqual(self.calls("osv-calls.txt"), [])

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
    """The Python lane is pinned and measures branches on BOTH paths."""

    FAKES = 'pip() { echo "pip $*" >> py-calls.txt; }\npython() { echo "python $*" >> py-calls.txt; }\n'

    def _run(self):
        env = {"PYCOVCFG": "true", "PYTESTS": "true"}
        return self.run_step("gate-tests-coverage python", env, self.FAKES)

    def test_unittest_path_measures_branches(self) -> None:
        """Line-only coverage could pass 100% with untested branches."""
        self.write("pyproject.toml", "[tool.coverage.run]\nbranch = true\n")
        self.write("pkg/__init__.py")
        self.write("pkg/tests/__init__.py")
        self.write("pkg/tests/test_x.py")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        pins = tool_pins()
        self.assertIn(f"pip install coverage=={pins['coverage']}", self.calls("py-calls.txt"))
        self.assertIn("python -m coverage run --branch -m unittest discover -s .", self.calls("py-calls.txt"))

    def test_unittest_path_discovers_a_non_package_tests_dir(self) -> None:
        """A root discover never enters a tests/ dir without __init__.py."""
        self.write("pyproject.toml", "[tool.coverage.run]\nbranch = true\n")
        self.write("tests/test_x.py")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unittest discover start dir: tests", result.stdout)
        self.assertIn("python -m coverage run --branch -m unittest discover -s tests", self.calls("py-calls.txt"))

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
        # python3 runs the REAL planner; the coverage assertion is recorded.
        return (
            self.FAKE_NODE
            + "python3() {\n"
            + '  case "$1" in\n'
            + f'    *jsts_projects.py) shift; "{PYTHON}" "$PLANNER" "$@" ;;\n'
            + '    *) echo "assert $*" >> assert-calls.txt ;;\n'
            + "  esac\n"
            + "}\n"
        )

    def _run(self, *, tests: bool = True):
        env = {
            "JSTSTESTS": "true" if tests else "false",
            "JSTS_COVERAGE_MIN": "100",
            "PLANNER": str(PLANNER).replace("\\", "/"),
        }
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

    def test_workspace_member_is_measured_by_the_root_run(self) -> None:
        """A member of a measured root workspace needs no script of its own."""
        self.write(
            "package.json",
            '{"workspaces": ["packages/*"], "scripts": {"test:coverage": "vitest run --coverage"}}',
        )
        self.write("packages/ui/package.json", '{"name": "ui"}')
        self.write("packages/ui/button.test.ts")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._measured_dirs(), ["."])
        self.assertIn("measured by the root workspace coverage run", result.stdout)

    def test_project_without_tests_is_reported_not_failed(self) -> None:
        """A tooling package with no tests has nothing to measure."""
        self.write("package.json", '{"scripts": {"test:coverage": "vitest run --coverage"}}')
        self.write("src/a.test.ts")
        self.write("tools/package.json", '{"name": "tools"}')
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("project tools: no JS/TS test files - nothing to measure.", result.stdout)

    def test_no_test_surface_is_still_a_documented_skip(self) -> None:
        """Source with zero test files keeps its narrow skip."""
        self.write("package.json", '{"name": "lib"}')
        self.write("index.js")
        result = self._run(tests=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no test surface by design", result.stdout)

    def test_tests_without_any_coverage_lane_fail(self) -> None:
        """Tests outside every project, and no lane anywhere: unmeasured."""
        self.write("lib/index.js")
        self.write("lib/index.test.js")
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no coverage script in any project dir", result.stderr)


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
