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

import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

import yaml  # type: ignore[import-untyped]
from tests.lean_gate_support import find_bash, run_step_script

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

    def _run(self, resolve: str, enabled: str, analyze: str) -> "subprocess.CompletedProcess[str]":
        bash = find_bash()
        if bash is None:  # pragma: no cover - environment guard
            self.skipTest("no real bash found")
        script = str(self.gate["steps"][0]["run"])
        with tempfile.TemporaryDirectory() as tmp:
            return run_step_script(
                script,
                workdir=Path(tmp),
                bash=bash,
                env_overrides={"RESOLVE_RESULT": resolve, "CODEQL_ENABLED": enabled, "ANALYZE_RESULT": analyze},
            )

    def test_a_failed_profile_resolution_fails_the_gate(self) -> None:
        """THE hole: this is exactly the case that used to read as passing."""
        for resolve in ("failure", "cancelled", "skipped"):
            with self.subTest(resolve=resolve):
                result = self._run(resolve, "", "skipped")
                self.assertEqual(result.returncode, 1)
                self.assertIn("could not be resolved", result.stdout)

    def test_a_declared_opt_out_passes_and_says_so(self) -> None:
        """A profile that disables CodeQL passes visibly, not silently."""
        result = self._run("success", "false", "skipped")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::notice title=CodeQL gate::", result.stdout)

    def test_the_analysis_must_succeed(self) -> None:
        """Enabled + anything but a successful analysis fails."""
        for analyze in ("failure", "cancelled", "skipped"):
            with self.subTest(analyze=analyze):
                self.assertEqual(self._run("success", "true", analyze).returncode, 1)
        self.assertEqual(self._run("success", "true", "success").returncode, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
