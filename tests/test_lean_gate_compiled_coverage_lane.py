"""Behavioural tests for the gate-3 Go / C++ coverage lane.

round-6 of ``reusable-quality.yml`` stopped running the caller's pre-commit and
dropped ``setup-go`` / ``setup-dotnet`` / ``setup-java`` / ``rust-toolchain``,
which silently removed the ONLY path by which a compiled language reached gate 3.
Measured consequence: ``DevExtreme-Filter-Go-Language`` is 236,928 bytes of Go
(``gh api repos/.../languages``) with 30+ root-level ``*_test.go`` files and a
``.pre-commit-config.yaml`` whose ``go-test-cover-100`` hook runs
``bash scripts/coverage-gate.sh`` -- and both coverage lanes early-``exit 0``
printing "no test surface by design" while the step reports success.
``Airline-Reservations-System`` is 190,349 bytes of C++ with ten ``*_test.cpp``
files under ``tests/`` and had no lane at all.

These tests do NOT assert on YAML text. They EXTRACT the lane's shell script from
the workflow and EXECUTE it under bash across the whole truth table, because the
load-bearing property is behavioural: **the "no test surface by design" skip must
be unreachable for a repo that demonstrably HAS test files in a language this
lane handles.**
"""

from __future__ import absolute_import

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Dict

import yaml
from tests.lean_gate_support import WORKFLOW, find_bash, run_step_script, step_named, workflow_steps

LANE_STEP_NAME = "gate-tests-coverage compiled (go / c++)"
SKIP_PHRASE = "no test surface by design"


def lane_step() -> Dict[str, object]:
    """Return the compiled-language coverage lane step."""
    return step_named(LANE_STEP_NAME)


class LaneWiringTests(unittest.TestCase):
    """Static contract: the lane is wired, guarded, and injection-safe."""

    def test_lane_exists_and_is_guarded_on_language_detection(self) -> None:
        """The lane runs when Go OR C++ source is present, and not otherwise."""
        step = lane_step()
        self.assertEqual(
            step.get("if"),
            "steps.detect.outputs.go == 'true' || steps.detect.outputs.cpp == 'true'",
        )

    def test_lane_reads_its_inputs_through_env_not_inline_expressions(self) -> None:
        """GitHub expands ``${{ }}`` textually into run blocks -- a real injection sink."""
        step = lane_step()
        self.assertNotIn("${{", str(step["run"]))
        self.assertEqual(
            dict(step["env"]),  # type: ignore[arg-type]
            {
                "GO_PRESENT": "${{ steps.detect.outputs.go }}",
                "GO_TESTS": "${{ steps.detect.outputs.gotests }}",
                "GO_MOD": "${{ steps.detect.outputs.gomod }}",
                "CPP_PRESENT": "${{ steps.detect.outputs.cpp }}",
                "CPP_TESTS": "${{ steps.detect.outputs.cpptests }}",
                "COVERAGE_GATE_SCRIPT": "${{ inputs.coverage-gate-script }}",
            },
        )

    def test_go_toolchain_is_restored_and_sha_pinned(self) -> None:
        """A Go coverage script cannot run without a Go toolchain."""
        setup = [s for s in workflow_steps() if str(s.get("uses", "")).startswith("actions/setup-go@")]
        self.assertEqual(len(setup), 1, "expected exactly one setup-go step")
        self.assertEqual(setup[0]["uses"], "actions/setup-go@40f1582b2485089dde7abd97c1529aa768e1baff")
        self.assertEqual(setup[0].get("if"), "steps.detect.outputs.gomod == 'true'")
        self.assertEqual(dict(setup[0]["with"])["go-version-file"], "go.mod")  # type: ignore[arg-type]
        # PyYAML strips the trailing comment, so assert the repo's
        # "<sha> # <tag>" pinning convention against the raw text.
        self.assertIn(
            "actions/setup-go@40f1582b2485089dde7abd97c1529aa768e1baff # v5.6.0",
            WORKFLOW.read_text(encoding="utf-8"),
        )

    def test_detection_emits_the_new_language_outputs(self) -> None:
        """The lane's guards are only as good as the detect step feeding them."""
        detect = next(s for s in workflow_steps() if s.get("id") == "detect")
        script = str(detect["run"])
        for expected in ('echo "go=', 'echo "gotests=', 'echo "gomod=', 'echo "cpp=', 'echo "cpptests='):
            self.assertIn(expected, script, expected)

    def test_go_test_detection_matches_the_canonical_go_test_suffix(self) -> None:
        """``*_test.go`` is the only Go test filename convention."""
        detect = next(s for s in workflow_steps() if s.get("id") == "detect")
        self.assertIn("'*_test.go'", str(detect["run"]))

    def test_cpp_test_detection_matches_the_measured_airline_layout(self) -> None:
        """Airline's tests are ``tests/<name>_test.cpp`` -- both shapes must match."""
        script = str(next(s for s in workflow_steps() if s.get("id") == "detect")["run"])
        self.assertIn("'**/*_test.cpp'", script)
        self.assertIn("'tests/**/*.cpp'", script)


class LaneBehaviourTests(unittest.TestCase):
    """Execute the lane's real shell script across the full truth table."""

    def setUp(self) -> None:
        """Materialise the lane script and a scratch caller repo."""
        self.bash = find_bash()
        if self.bash is None:  # pragma: no cover - environment guard
            self.skipTest("no real bash found; cannot execute the lane script")
        self.workdir = Path(tempfile.mkdtemp(prefix="qzp-lane-"))
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.lane_script = str(lane_step()["run"])
        self.counter = self.workdir / "gate-runs.txt"

    def _write_coverage_gate(self, exit_code: int) -> str:
        """Write a fake caller coverage gate that records each invocation."""
        target = self.workdir / "scripts" / "coverage-gate.sh"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "#!/usr/bin/env bash\necho ran >> gate-runs.txt\necho 'caller coverage gate'\nexit "
            + str(exit_code)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return "scripts/coverage-gate.sh"

    def _run(
        self,
        *,
        go: str = "false",
        gotests: str = "false",
        gomod: str = "false",
        cpp: str = "false",
        cpptests: str = "false",
        gate_script: str = "scripts/coverage-gate.sh",
    ) -> subprocess.CompletedProcess:
        """Run the lane script with an explicit detect-output environment."""
        assert self.bash is not None
        return run_step_script(
            self.lane_script,
            workdir=self.workdir,
            bash=self.bash,
            filename="lane.sh",
            env_overrides={
                "GO_PRESENT": go,
                "GO_TESTS": gotests,
                "GO_MOD": gomod,
                "CPP_PRESENT": cpp,
                "CPP_TESTS": cpptests,
                "COVERAGE_GATE_SCRIPT": gate_script,
            },
        )

    def _gate_run_count(self) -> int:
        """How many times the fake caller coverage gate was invoked."""
        if not self.counter.exists():
            return 0
        return len([line for line in self.counter.read_text(encoding="utf-8").splitlines() if line.strip()])

    # ---- the documented narrow skip, which must stay reachable -------------

    def test_go_source_without_tests_skips(self) -> None:
        """Go source with ZERO ``*_test.go`` files is the documented skip."""
        result = self._run(go="true", gomod="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(SKIP_PHRASE, result.stdout)

    def test_cpp_source_without_tests_skips(self) -> None:
        """C++ source with ZERO test files is the documented skip."""
        result = self._run(cpp="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(SKIP_PHRASE, result.stdout)

    # ---- the CRITICAL property: the skip must be UNREACHABLE with tests ----

    def test_go_tests_present_makes_the_skip_unreachable(self) -> None:
        """THE REGRESSION: DevExtreme has 30+ *_test.go and was skipped green."""
        result = self._run(go="true", gotests="true", gomod="true", gate_script="scripts/coverage-gate.sh")
        self.assertNotIn(SKIP_PHRASE, result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL gate-tests-coverage", result.stdout + result.stderr)

    def test_cpp_tests_present_makes_the_skip_unreachable(self) -> None:
        """Airline has ten *_test.cpp files and had no lane at all."""
        result = self._run(cpp="true", cpptests="true")
        self.assertNotIn(SKIP_PHRASE, result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL gate-tests-coverage", result.stdout + result.stderr)

    def test_go_tests_without_a_go_mod_fail_rather_than_skip(self) -> None:
        """No go.mod means no toolchain was set up -- that is a hole, not a skip."""
        self._write_coverage_gate(0)
        result = self._run(go="true", gotests="true", gomod="false")
        self.assertNotIn(SKIP_PHRASE, result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("go.mod", result.stdout + result.stderr)
        self.assertEqual(self._gate_run_count(), 0, "the caller gate must not run without a toolchain")

    # ---- the pass path -----------------------------------------------------

    def test_go_tests_with_a_passing_caller_gate_pass(self) -> None:
        """DevExtreme's own coverage gate becomes the measured lane."""
        self._write_coverage_gate(0)
        result = self._run(go="true", gotests="true", gomod="true")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(SKIP_PHRASE, result.stdout)
        self.assertIn("PASS gate-tests-coverage", result.stdout)
        self.assertEqual(self._gate_run_count(), 1)

    def test_a_failing_caller_gate_reds_the_lane(self) -> None:
        """DETECTOR CONTROL: the lane must go red when the coverage gate fails."""
        self._write_coverage_gate(1)
        result = self._run(go="true", gotests="true", gomod="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._gate_run_count(), 1)

    def test_go_and_cpp_together_run_the_caller_gate_exactly_once(self) -> None:
        """A mixed repo must not pay for the same coverage run twice."""
        self._write_coverage_gate(0)
        result = self._run(go="true", gotests="true", gomod="true", cpp="true", cpptests="true")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self._gate_run_count(), 1)
        self.assertIn("go", result.stdout)
        self.assertIn("c++", result.stdout)

    def test_a_custom_gate_script_path_is_honoured(self) -> None:
        """``coverage-gate-script`` lets a differently-laid-out repo reach green."""
        custom = self.workdir / "ci" / "cover.sh"
        custom.parent.mkdir(parents=True, exist_ok=True)
        custom.write_text("#!/usr/bin/env bash\necho ran >> gate-runs.txt\nexit 0\n", encoding="utf-8", newline="\n")
        result = self._run(go="true", gotests="true", gomod="true", gate_script="ci/cover.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self._gate_run_count(), 1)

    def test_the_failure_message_names_the_exact_remediation(self) -> None:
        """A gate the owner cannot drive to green is a defect; name the fix."""
        result = self._run(cpp="true", cpptests="true")
        combined = result.stdout + result.stderr
        self.assertIn("scripts/coverage-gate.sh", combined)
        self.assertIn("coverage-gate-script", combined)


class LaneInputTests(unittest.TestCase):
    """The lane's remediation path must be declared as a workflow input."""

    def test_coverage_gate_script_input_is_declared_with_the_fleet_default(self) -> None:
        """The default is the convention DevExtreme's own pre-commit hook calls."""
        document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        # ``on`` parses to the boolean True under the YAML 1.1 rules PyYAML uses.
        triggers = document.get("on", document.get(True))
        spec = triggers["workflow_call"]["inputs"]["coverage-gate-script"]
        self.assertEqual(spec["default"], "scripts/coverage-gate.sh")
        self.assertEqual(spec["type"], "string")
        self.assertFalse(spec["required"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
