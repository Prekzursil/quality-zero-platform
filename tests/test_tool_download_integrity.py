"""Tool downloads are fail-fast, digest-verified and script-free.

The lean gate, the platform's own SAST job and the standing CVE sweep install
opengrep, gitleaks and osv-scanner straight from GitHub release assets. Before
this contract, opengrep came from ``curl .../opengrep/main/install.sh | bash``
(a remote script from a moving branch) and gitleaks/osv-scanner from
``curl -sSL`` with no ``-f`` and no digest check, so a tampered or truncated
asset -- or an HTTP error page saved as a binary -- would have been installed.

The static tests pin the shape (``curl -f`` + ``sha256sum -c`` before install,
digests and versions equal to ``.quality/tool-pins.yml``). The behavioural
tests EXTRACT each lean-gate install step and EXECUTE it under bash with a fake
``curl``: a tampered asset or an HTTP error must fail the step and leave
nothing installed; the genuine asset must install.
"""

from __future__ import absolute_import

import hashlib
import io
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List

import yaml
from tests.lean_gate_support import ROOT, find_bash, run_step_script, step_named

WORKFLOWS = ROOT / ".github" / "workflows"
PINS = ROOT / ".quality" / "tool-pins.yml"
# Workflows that install tools by downloading release assets.
DOWNLOADING = (
    "reusable-quality.yml",
    "quality-zero-platform.yml",
    "scheduled-cve-scan.yml",
    "reusable-scanner-matrix.yml",
)
# A remote script piped into a shell. The last instance (the legacy scanner
# matrix's DeepSource CLI installer) is gone: no workflow may pipe one again.
PIPE_TO_SHELL = re.compile(r"curl[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b")
KNOWN_PIPE_TO_SHELL: Dict[str, int] = {}
DIGEST_CHECK = re.compile(r'echo "([0-9a-f]{64})  ([^"]+)" \| sha256sum -c -')
# Stand-in for curl, defined as a shell FUNCTION ahead of the step body: a
# function wins over every PATH lookup, including Git for Windows' bash.exe
# wrapper, which forces /mingw64/bin (the real curl) to the front of PATH.
# Honours -o <path>; copies $FAKE_CURL_PAYLOAD there, or fails the way
# `curl -f` does on an HTTP error (22) when told to.
FAKE_CURL = """curl() {
  local out=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -o) out="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  if [ "${FAKE_CURL_HTTP_ERROR:-0}" = "1" ]; then
    return 22
  fi
  cp "$FAKE_CURL_PAYLOAD" "$out"
}
"""


def tool_pins() -> Dict[str, Dict[str, str]]:
    """The parsed ``.quality/tool-pins.yml``."""
    return yaml.safe_load(PINS.read_text(encoding="utf-8"))


def downloading_steps(workflow: str) -> List[Dict[str, object]]:
    """Every step of ``workflow`` whose shell body runs curl."""
    document = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    steps = []
    for job in document["jobs"].values():
        for step in job.get("steps", []):
            if "curl " in str(step.get("run", "")):
                steps.append(step)
    return steps


def curl_targets(script: str) -> List[str]:
    """The ``-o`` targets of every curl in ``script`` (continuations joined)."""
    joined = script.replace("\\\n", " ")
    targets = []
    for line in joined.splitlines():
        if re.search(r"\bcurl\b", line):
            match = re.search(r"\s-o\s+(\S+)", line)
            if match:
                targets.append(match.group(1).strip('"'))
    return targets


class DownloadContractTests(unittest.TestCase):
    """Static contract over every workflow that downloads a tool."""

    def test_no_remote_script_is_piped_into_a_shell(self) -> None:
        """``curl ... | bash`` executes whatever the URL serves at run time."""
        found = {}
        for path in sorted(WORKFLOWS.glob("*.yml")):
            hits = PIPE_TO_SHELL.findall(path.read_text(encoding="utf-8"))
            if hits:
                found[path.name] = len(hits)
        self.assertEqual(found, KNOWN_PIPE_TO_SHELL)

    def test_every_download_fails_fast_and_is_verified_before_install(self) -> None:
        """``curl -f`` and a ``sha256sum -c`` of the exact target precede any use."""
        checked = 0
        for workflow in DOWNLOADING:
            for step in downloading_steps(workflow):
                script = str(step["run"])
                lines = script.replace("\\\n", " ").splitlines()
                for target in curl_targets(script):
                    curl_line = next(
                        i for i, line in enumerate(lines) if re.search(r"\bcurl\b", line) and target in line
                    )
                    self.assertRegex(lines[curl_line], r"curl -[a-zA-Z]*f", f"{workflow}: {step['name']}")
                    verify_line = next(
                        (
                            i
                            for i, line in enumerate(lines)
                            if (m := DIGEST_CHECK.search(line)) and m.group(2) == target
                        ),
                        None,
                    )
                    self.assertIsNotNone(verify_line, f"{workflow}: {step['name']} never verifies {target}")
                    for i, line in enumerate(lines):
                        uses_target = target in line and i not in (curl_line, verify_line)
                        if uses_target:
                            self.assertGreater(i, verify_line, f"{workflow}: {step['name']} uses {target} unverified")
                    checked += 1
        self.assertEqual(checked, 6)  # lean gate x3, self-gate SAST, CVE sweep, legacy DeepSource CLI

    def test_the_deepsource_cli_is_the_pinned_release_asset(self) -> None:
        """The legacy matrix verifies one exact DeepSource CLI tarball before unpacking it."""
        (step,) = downloading_steps("reusable-scanner-matrix.yml")
        script = str(step["run"])
        self.assertIn("DeepSourceCorp/cli/releases/download/v0.10.1/deepsource_0.10.1_linux_amd64.tar.gz", script)
        self.assertEqual(
            [m.group(1) for m in DIGEST_CHECK.finditer(script)],
            ["e415a6a53257457ef1dad900fdb39aeb991dd01c35c4be9cafa88a41f9e50b3b"],
        )
        self.assertNotIn("./bin/deepsource", script)

    def test_lean_gate_digests_and_versions_match_tool_pins(self) -> None:
        """``.quality/tool-pins.yml`` is the single source of truth for both."""
        pins = tool_pins()
        expected = {
            "Install opengrep": (
                f"opengrep/releases/download/{pins['pins']['opengrep']}/opengrep_manylinux_x86",
                pins["asset_sha256"]["opengrep_manylinux_x86"],
            ),
            "Install gitleaks": (
                f"gitleaks/releases/download/v{pins['pins']['gitleaks']}/gitleaks_{pins['pins']['gitleaks']}_linux_x64.tar.gz",
                pins["asset_sha256"]["gitleaks_linux_x64"],
            ),
            "Install osv-scanner": (
                f"osv-scanner/releases/download/{pins['pins']['osv-scanner']}/osv-scanner_linux_amd64",
                pins["asset_sha256"]["osv-scanner_linux_amd64"],
            ),
        }
        for name, (url_part, digest) in expected.items():
            script = str(step_named(name)["run"])
            self.assertIn(url_part, script, name)
            self.assertEqual([m.group(1) for m in DIGEST_CHECK.finditer(script)], [digest], name)

    def test_the_platform_runs_the_sast_engine_it_enforces(self) -> None:
        """QZP's own SAST job and the fleet's gate 4 fetch the identical pinned opengrep asset.

        They used to drift (the self-gate on v1.23.0, the fleet on v1.22.0): the
        platform was not held to exactly what it enforces.
        """
        pins = tool_pins()
        (step,) = [s for s in downloading_steps("quality-zero-platform.yml") if "opengrep" in str(s["run"])]
        script = str(step["run"])
        self.assertIn(f"opengrep/releases/download/{pins['pins']['opengrep']}/opengrep_manylinux_x86", script)
        self.assertEqual(
            [m.group(1) for m in DIGEST_CHECK.finditer(script)], [pins["asset_sha256"]["opengrep_manylinux_x86"]]
        )

    def test_cve_sweep_verifies_the_same_osv_scanner_asset_as_the_gate(self) -> None:
        """The standing sweep and gate 6 must run the identical binary."""
        digest = tool_pins()["asset_sha256"]["osv-scanner_linux_amd64"]
        (step,) = downloading_steps("scheduled-cve-scan.yml")
        self.assertEqual([m.group(1) for m in DIGEST_CHECK.finditer(str(step["run"]))], [digest])

    def test_secrets_gate_lists_findings_but_keeps_them_redacted(self) -> None:
        """A red gate-5 must say which rule and file fired, never the secret."""
        script = str(step_named("gate-secrets gitleaks (self-contained)")["run"])
        self.assertIn("args=(detect --no-banner --redact --verbose)", script)


class InstallBehaviourTests(unittest.TestCase):
    """Execute the real lean-gate install steps against a fake ``curl``."""

    def setUp(self) -> None:
        """A scratch dir holding the payload and the relocated install roots."""
        self.bash = find_bash()
        if self.bash is None:  # pragma: no cover - environment guard
            self.skipTest("no real bash found; cannot execute the install steps")
        self.workdir = Path(tempfile.mkdtemp(prefix="qzp-download-"))
        self.addCleanup(shutil.rmtree, self.workdir, True)
        for sub in ("tmp", "bin", "home"):
            (self.workdir / sub).mkdir()

    def _payload(self, data: bytes) -> str:
        path = self.workdir / "payload"
        path.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def _run(
        self, step_name: str, *, genuine_digest: str = "", http_error: bool = False
    ) -> subprocess.CompletedProcess:
        """Run ``step_name`` with its install roots relocated into the workdir.

        ``genuine_digest`` swaps the pinned digest for the payload's own (the
        "untampered asset" case); left empty, the pinned digest stays, which a
        fake payload can never match (the "tampered asset" case)."""
        assert self.bash is not None
        script = str(step_named(step_name)["run"])
        script = script.replace("/usr/local/bin", "./bin").replace("/tmp/", "./tmp/")
        if genuine_digest:
            script = DIGEST_CHECK.sub(lambda m: f'echo "{genuine_digest}  {m.group(2)}" | sha256sum -c -', script)
        return run_step_script(
            FAKE_CURL + script,
            workdir=self.workdir,
            bash=self.bash,
            filename="install.sh",
            env_overrides={
                "HOME": "./home",
                "GITHUB_PATH": "./github-path.txt",
                "FAKE_CURL_PAYLOAD": "./payload",
                "FAKE_CURL_HTTP_ERROR": "1" if http_error else "0",
            },
        )

    def _gitleaks_tarball(self) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            body = b"#!/bin/sh\necho fake gitleaks\n"
            info = tarfile.TarInfo("gitleaks")
            info.size = len(body)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(body))
        return buffer.getvalue()

    # ---- gitleaks -----------------------------------------------------------

    def test_gitleaks_tampered_asset_fails_and_installs_nothing(self) -> None:
        self._payload(self._gitleaks_tarball())
        result = self._run("Install gitleaks")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse((self.workdir / "bin" / "gitleaks").exists())

    def test_gitleaks_http_error_fails_and_installs_nothing(self) -> None:
        self._payload(self._gitleaks_tarball())
        result = self._run("Install gitleaks", http_error=True)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse((self.workdir / "bin" / "gitleaks").exists())

    def test_gitleaks_genuine_asset_installs(self) -> None:
        digest = self._payload(self._gitleaks_tarball())
        result = self._run("Install gitleaks", genuine_digest=digest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.workdir / "bin" / "gitleaks").is_file())

    # ---- osv-scanner --------------------------------------------------------

    def test_osv_scanner_tampered_asset_fails_and_installs_nothing(self) -> None:
        self._payload(b"#!/bin/sh\necho not the pinned osv-scanner\n")
        result = self._run("Install osv-scanner")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse((self.workdir / "bin" / "osv-scanner").exists())

    def test_osv_scanner_genuine_asset_installs(self) -> None:
        digest = self._payload(b"#!/bin/sh\necho osv-scanner\n")
        result = self._run("Install osv-scanner", genuine_digest=digest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.workdir / "bin" / "osv-scanner").is_file())

    # ---- opengrep -----------------------------------------------------------

    def test_opengrep_tampered_asset_fails_and_installs_nothing(self) -> None:
        self._payload(b"#!/bin/sh\necho not the pinned opengrep\n")
        result = self._run("Install opengrep")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        latest = self.workdir / "home" / ".opengrep" / "cli" / "latest"
        self.assertFalse((latest / "opengrep").exists())
        self.assertFalse((self.workdir / "github-path.txt").exists())

    def test_opengrep_genuine_asset_installs_onto_path(self) -> None:
        digest = self._payload(b"#!/bin/sh\necho opengrep\n")
        result = self._run("Install opengrep", genuine_digest=digest)
        self.assertEqual(result.returncode, 0, result.stderr)
        latest = self.workdir / "home" / ".opengrep" / "cli" / "latest"
        self.assertTrue((latest / "opengrep").is_file())
        self.assertFalse((latest / "opengrep.download").exists())
        # $HOME/.opengrep/cli/latest (MSYS bash may absolutise the relative HOME)
        on_path = (self.workdir / "github-path.txt").read_text(encoding="utf-8").strip()
        self.assertTrue(on_path.endswith("home/.opengrep/cli/latest"), on_path)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
