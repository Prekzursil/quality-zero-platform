"""Behavioural tests for gate 5 (secrets) running ALWAYS.

The trigger was inverted: gate 5 ran only when ``.gitleaks.toml`` existed, and
``.gitleaks.toml`` is an ALLOWLIST. So the presence of "things we have decided to
ignore" was the opt-in signal for secret scanning, and a repo that had never
needed an allowlist -- a clean repo, or a brand-new adopter -- got no secret
scanning at all. Secret scanning is T0 absolute-zero: it must be always-on with
the allowlist optional.

Blast radius measured live on 2026-08-11 (`gh api repos/Prekzursil/<r>/contents/
.gitleaks.toml`, with quality-zero-platform itself returning 404 as the detector
control): all 14 governed repos that ship a `quality.yml` calling this workflow
ALREADY have a `.gitleaks.toml`, so this inversion changes nothing for today's
callers. What it fixes is the structural hole for the next repo.
"""

from __future__ import absolute_import

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.lean_gate_support import find_bash, run_step_script, step_named, workflow_steps

GATE_STEP_NAME = "gate-secrets gitleaks (self-contained)"
INSTALL_STEP_NAME = "Install gitleaks"


class SecretsGateWiringTests(unittest.TestCase):
    """The gate and its installer must both be unconditional."""

    def test_the_secrets_gate_has_no_conditional_guard(self) -> None:
        """An allowlist must not be the opt-in signal for scanning."""
        self.assertNotIn("if", step_named(GATE_STEP_NAME))

    def test_the_gitleaks_installer_has_no_conditional_guard(self) -> None:
        """A gate that always runs needs a binary that is always installed."""
        self.assertNotIn("if", step_named(INSTALL_STEP_NAME))

    def test_the_gitleaks_pin_is_unchanged(self) -> None:
        """This commit inverts a trigger; it does not move a pin."""
        self.assertIn("v8.30.1", str(step_named(INSTALL_STEP_NAME)["run"]))

    def test_detection_still_reports_whether_an_allowlist_exists(self) -> None:
        """The allowlist is now an input to the scan, not a guard on it."""
        detect = next(s for s in workflow_steps() if s.get("id") == "detect")
        self.assertIn('echo "gitleaksallowlist=', str(detect["run"]))

    def test_the_gate_reads_the_allowlist_flag_through_env(self) -> None:
        """GitHub expands ``${{ }}`` textually into run blocks."""
        step = step_named(GATE_STEP_NAME)
        self.assertNotIn("${{", str(step["run"]))
        self.assertEqual(
            dict(step["env"]),  # type: ignore[arg-type]
            {"GITLEAKS_ALLOWLIST": "${{ steps.detect.outputs.gitleaksallowlist }}"},
        )


class SecretsGateBehaviourTests(unittest.TestCase):
    """Execute the gate's real shell script against a recording fake gitleaks."""

    def setUp(self) -> None:
        """Put a fake ``gitleaks`` on PATH that records its argv."""
        self.bash = find_bash()
        if self.bash is None:  # pragma: no cover - environment guard
            self.skipTest("no real bash found; cannot execute the gate script")
        self.workdir = Path(tempfile.mkdtemp(prefix="qzp-secrets-"))
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.binroot = self.workdir / "fakebin"
        self.binroot.mkdir()
        self.argv_log = self.workdir / "gitleaks-argv.txt"
        self.gate_script = str(step_named(GATE_STEP_NAME)["run"])

    def _install_fake_gitleaks(self, exit_code: int) -> None:
        """Write the recording stub."""
        stub = self.binroot / "gitleaks"
        stub.write_text(
            '#!/usr/bin/env bash\necho "$@" >> gitleaks-argv.txt\nexit ' + str(exit_code) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        stub.chmod(0o755)

    def _run(self, *, allowlist: str, exit_code: int = 0) -> subprocess.CompletedProcess:
        """Run the gate script with the fake gitleaks first on PATH."""
        self._install_fake_gitleaks(exit_code)
        assert self.bash is not None
        import os as _os

        return run_step_script(
            self.gate_script,
            workdir=self.workdir,
            bash=self.bash,
            filename="secrets.sh",
            env_overrides={
                "GITLEAKS_ALLOWLIST": allowlist,
                "PATH": str(self.binroot) + _os.pathsep + _os.environ.get("PATH", ""),
            },
        )

    def _recorded_argv(self) -> str:
        """Return everything the fake gitleaks was invoked with."""
        if not self.argv_log.exists():
            return ""
        return self.argv_log.read_text(encoding="utf-8")

    def test_it_scans_even_without_an_allowlist(self) -> None:
        """THE INVERSION: a repo with no .gitleaks.toml is now scanned."""
        result = self._run(allowlist="false")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("detect", self._recorded_argv())
        self.assertNotIn("--config", self._recorded_argv())

    def test_it_honours_an_allowlist_when_one_exists(self) -> None:
        """An existing allowlist keeps working exactly as before."""
        result = self._run(allowlist="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--config .gitleaks.toml", self._recorded_argv())

    def test_a_finding_still_reds_the_gate_without_an_allowlist(self) -> None:
        """DETECTOR CONTROL: an always-on gate that cannot go red is a no-op."""
        result = self._run(allowlist="false", exit_code=1)
        self.assertNotEqual(result.returncode, 0)

    def test_a_finding_still_reds_the_gate_with_an_allowlist(self) -> None:
        """Allowlisting narrows the ruleset; it does not disarm the gate."""
        result = self._run(allowlist="true", exit_code=1)
        self.assertNotEqual(result.returncode, 0)

    def test_the_redaction_flags_are_never_dropped(self) -> None:
        """A secret must never be echoed into a public job log."""
        self._run(allowlist="false")
        self.assertIn("--redact", self._recorded_argv())
        self.assertIn("--no-banner", self._recorded_argv())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
