"""Contract tests for ``reusable-codeql.yml``.

Pins the cross-repo invariants the reusable CodeQL workflow MUST hold
so that fleet consumers can pin it without shipping platform-only
artefacts:

* ``codeql_config_file`` is an OPTIONAL input with empty-string default
  so consumers don't need to ship ``.github/codeql/codeql-config.yml``.
* The platform's own ``codeql.yml`` caller passes the config-file path
  explicitly; consumers leave it empty.

These invariants exist because PR #119 introduced a
``config-file: ./.github/codeql/codeql-config.yml`` hardcode in the
reusable workflow, breaking every fleet consumer that didn't ship that
file (event-link PR #130 first surfaced the regression: ``The
configuration file ".github/codeql/codeql-config.yml" does not exist``).
"""

from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict

import yaml  # type: ignore[import-untyped]
from tests.lean_gate_support import find_bash, run_step_script

from scripts.quality import profile_contract_validation
from scripts.quality.control_plane import load_inventory, load_repo_profile, validate_profile

if TYPE_CHECKING:
    import subprocess

_REUSABLE = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "reusable-codeql.yml"
_CALLER = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "codeql.yml"


class ReusableCodeQLContract(unittest.TestCase):
    """Reusable workflow stays consumer-safe."""

    @classmethod
    def setUpClass(cls) -> None:
        """Parse both workflow YAMLs once."""
        cls.reusable = yaml.safe_load(_REUSABLE.read_text(encoding="utf-8"))
        cls.caller = yaml.safe_load(_CALLER.read_text(encoding="utf-8"))

    def test_codeql_config_file_input_is_optional_with_empty_default(self) -> None:
        """``codeql_config_file`` is optional + defaults to empty string."""
        # ``on:`` parses as YAML boolean True under PyYAML.
        inputs = self.reusable[True]["workflow_call"]["inputs"]
        self.assertIn("codeql_config_file", inputs)
        self.assertFalse(inputs["codeql_config_file"].get("required", False))
        # Default must be the EMPTY string so the actions/codeql ``init``
        # step skips ``--config-file`` entirely when no path is wired.
        self.assertEqual(inputs["codeql_config_file"].get("default"), "")

    def test_init_step_uses_codeql_config_file_input(self) -> None:
        """``codeql-action/init`` ``config-file`` references the input, not a hardcode."""
        steps = self.reusable["jobs"]["codeql"]["steps"]
        init_steps = [s for s in steps if "uses" in s and s["uses"].startswith("github/codeql-action/init")]
        self.assertEqual(len(init_steps), 1)
        config_value = init_steps[0]["with"].get("config-file")
        self.assertEqual(
            config_value,
            "${{ inputs.codeql_config_file }}",
            "init step must thread the input through, not hardcode a "
            "platform-only path that fleet consumers don't have",
        )

    def test_platform_caller_passes_config_file_path(self) -> None:
        """Platform's own ``codeql.yml`` caller passes the config-file path."""
        caller_with = self.caller["jobs"]["codeql"].get("with", {}) or {}
        cfg = caller_with.get("codeql_config_file", "")
        self.assertIn(
            "codeql-config.yml",
            cfg,
            "platform's CALLER must pass the config-file path so the "
            "platform's own CodeQL run keeps its false-positive "
            "suppressions for secrets_sync.py",
        )


class CodeQLGateTests(unittest.TestCase):
    """`<caller> / CodeQL` is a fail-closed gate, never a skippable job.

    The required check used to be the analysis job itself, behind
    ``if: enabled == 'true'``. A failed or empty profile resolution SKIPPED it,
    and GitHub counts a skipped required check as passing.
    """

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the reusable workflow once."""
        cls.reusable = yaml.safe_load(_REUSABLE.read_text(encoding="utf-8"))
        cls.gate = cls.reusable["jobs"]["codeql-gate"]

    def test_gate_owns_the_required_check_name_and_always_runs(self) -> None:
        """Static contract: name, always(), needs, no permissions."""
        self.assertEqual(self.gate["name"], "CodeQL")
        self.assertEqual(self.gate["if"], "${{ always() }}")
        self.assertEqual(self.gate["needs"], ["resolve-profile", "codeql"])
        self.assertEqual(self.gate["permissions"], {})

    def test_analysis_keeps_its_job_id_and_yields_the_check_name(self) -> None:
        """The analysis key (job id) is unchanged; only the display name moved."""
        analysis = self.reusable["jobs"]["codeql"]
        self.assertEqual(analysis["name"], "CodeQL analyze")
        self.assertEqual(analysis["if"], "${{ needs.resolve-profile.outputs.enabled == 'true' }}")

    def test_gate_decides_on_the_plan_state(self) -> None:
        """The gate reads the planner's explicit state, never the derived boolean."""
        self.assertEqual(
            self.reusable["jobs"]["resolve-profile"]["outputs"]["state"], "${{ steps.plan.outputs.codeql_state }}"
        )
        self.assertEqual(self.gate["steps"][0]["env"]["CODEQL_STATE"], "${{ needs.resolve-profile.outputs.state }}")

    def _run(self, resolve: str, state: str, analyze: str) -> "subprocess.CompletedProcess[str]":
        bash = find_bash()
        if bash is None:  # pragma: no cover - environment guard
            self.skipTest("no real bash found")
        script = str(self.gate["steps"][0]["run"])
        with tempfile.TemporaryDirectory() as tmp:
            return run_step_script(
                script,
                workdir=Path(tmp),
                bash=bash,
                env_overrides={"RESOLVE_RESULT": resolve, "CODEQL_STATE": state, "ANALYZE_RESULT": analyze},
            )

    def test_a_failed_profile_resolution_fails_the_gate(self) -> None:
        """THE hole: this is exactly the case that used to read as passing."""
        for resolve in ("failure", "cancelled", "skipped"):
            with self.subTest(resolve=resolve):
                result = self._run(resolve, "", "skipped")
                self.assertEqual(result.returncode, 1)
                self.assertIn("could not be resolved", result.stdout)

    def test_a_declared_opt_out_passes_and_says_so(self) -> None:
        """Only `codeql.enabled: false` passes without an analysis, and visibly."""
        result = self._run("success", "opt-out", "skipped")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::notice title=CodeQL gate::", result.stdout)
        self.assertIn("codeql.enabled: false", result.stdout)

    def test_enabled_without_languages_fails(self) -> None:
        """An enabled profile that analyses nothing is broken, not opted out."""
        result = self._run("success", "no-languages", "skipped")
        self.assertEqual(result.returncode, 1)
        self.assertIn("declares no codeql.languages", result.stdout)

    def test_an_unknown_or_missing_state_fails(self) -> None:
        """A planner that wrote no state (or a new one) never passes the gate."""
        for state in ("", "maybe"):
            with self.subTest(state=state):
                result = self._run("success", state, "skipped")
                self.assertEqual(result.returncode, 1)
                self.assertIn("Unknown CodeQL plan state", result.stdout)

    def test_the_analysis_must_succeed(self) -> None:
        """Enabled + anything but a successful analysis fails."""
        for analyze in ("failure", "cancelled", "skipped"):
            with self.subTest(analyze=analyze):
                self.assertEqual(self._run("success", "enabled", analyze).returncode, 1)
        self.assertEqual(self._run("success", "enabled", "success").returncode, 0)


class CodeQLPlanTests(unittest.TestCase):
    """The planner tells an opt-out apart from a profile with nothing to analyse."""

    @classmethod
    def setUpClass(cls) -> None:
        """Extract the real plan step from the reusable workflow."""
        reusable = yaml.safe_load(_REUSABLE.read_text(encoding="utf-8"))
        steps = reusable["jobs"]["resolve-profile"]["steps"]
        cls.script = str(next(step for step in steps if step.get("id") == "plan")["run"])

    def _plan(self, codeql: Dict[str, Any]) -> Dict[str, str]:
        bash = find_bash()
        if bash is None:  # pragma: no cover - environment guard
            self.skipTest("no real bash found")
        python = sys.executable.replace("\\", "/")
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / "profile.json").write_text(json.dumps({"codeql": codeql}), encoding="utf-8")
            output = workdir / "github-output.txt"
            result = run_step_script(
                f'python() {{ "{python}" "$@"; }}\n' + self.script,
                workdir=workdir,
                bash=bash,
                env_overrides={"RUNNER_TEMP": str(workdir), "GITHUB_OUTPUT": str(output)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = output.read_text(encoding="utf-8").splitlines()
        outputs: Dict[str, str] = {}
        for line in lines:
            key, _, value = line.partition("=")
            outputs[key] = value
        return outputs

    def test_declared_languages_are_enabled(self) -> None:
        """Languages plus the default `enabled` run the analysis."""
        outputs = self._plan({"languages": ["actions", "", "python"]})
        self.assertEqual(outputs["codeql_state"], "enabled")
        self.assertEqual(outputs["codeql_enabled"], "true")
        self.assertEqual(outputs["codeql_languages_csv"], "actions,python")

    def test_only_an_explicit_false_is_an_opt_out(self) -> None:
        """`enabled: false` opts out even when languages are declared."""
        outputs = self._plan({"enabled": False, "languages": ["python"]})
        self.assertEqual(outputs["codeql_state"], "opt-out")
        self.assertEqual(outputs["codeql_enabled"], "false")

    def test_enabled_without_languages_is_not_an_opt_out(self) -> None:
        """THE review finding: an empty language list used to read as an opt-out."""
        for codeql in ({"enabled": True, "languages": []}, {}, {"languages": [""]}):
            with self.subTest(codeql=codeql):
                outputs = self._plan(codeql)
                self.assertEqual(outputs["codeql_state"], "no-languages")
                self.assertEqual(outputs["codeql_enabled"], "false")


class CodeQLProfileContractTests(unittest.TestCase):
    """The same broken profile is reported while rendering, before any run."""

    def test_enabled_without_languages_is_a_finding(self) -> None:
        """Blank or missing languages under an enabled CodeQL section."""
        for codeql in ({"enabled": True, "languages": []}, {"languages": [" "]}, {"enabled": True}):
            with self.subTest(codeql=codeql):
                findings = profile_contract_validation._validate_codeql_contract({"slug": "o/r", "codeql": codeql})
                self.assertEqual(len(findings), 1)
                self.assertIn("o/r: codeql.languages is required while CodeQL is enabled", findings[0])

    def test_opt_out_declared_languages_or_no_section_pass(self) -> None:
        """Only an enabled section with nothing to analyse is reported."""
        for profile in (
            {"slug": "o/r", "codeql": {"enabled": False, "languages": []}},
            {"slug": "o/r", "codeql": {"enabled": True, "languages": ["python"]}},
            {"slug": "o/r", "codeql": "not-a-mapping"},
            {"slug": "o/r"},
        ):
            with self.subTest(profile=profile):
                self.assertEqual(profile_contract_validation._validate_codeql_contract(profile), [])

    def test_validate_profile_runs_the_codeql_contract(self) -> None:
        """Wired into the full profile validation every render runs."""
        profile = load_repo_profile(load_inventory(), "Prekzursil/quality-zero-platform")
        self.assertEqual([item for item in validate_profile(profile) if "codeql.languages" in item], [])
        profile["codeql"]["languages"] = []
        self.assertTrue(any("codeql.languages is required" in item for item in validate_profile(profile)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
