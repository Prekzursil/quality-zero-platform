"""Behavioural tests for the local preflight's tool provisioning.

``scripts/quality/preflight_tools.sh`` is SOURCED here under a real bash with a
fake ``curl`` (a shell function, so it wins over any PATH lookup -- Git for
Windows' bash.exe puts the real curl first), a scratch pin file and a relative
cache dir. Two defects it replaces are pinned as regressions:

* gitleaks was requested as ``<os>_amd64`` although its x86-64 release assets
  are named ``<os>_x64`` -- a 404, so gate 5 was SKIPPED on every x86-64 Linux
  and macOS machine;
* opengrep was installed by ``install.sh`` (which writes under
  ``$HOME/.opengrep``) while the caller looked in the cache dir -- so gate 4
  was always SKIPPED.

And the contract itself: nothing is installed unless its SHA-256 matches
``asset_sha256`` in the pin file.
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
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from tests.lean_gate_support import ROOT, find_bash, run_step_script

HELPER = ROOT / "scripts" / "quality" / "preflight_tools.sh"
PINS = ROOT / ".quality" / "tool-pins.yml"
FAKE_CURL = """curl() {
  local out="" url=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -o) out="$2"; shift 2 ;;
      -*) shift ;;
      *) url="$1"; shift ;;
    esac
  done
  echo "$url" >> ./curl.log
  if [ "${FAKE_HTTP_ERROR:-0}" = "1" ]; then
    return 22
  fi
  cp ./payload "$out"
}
"""


def _tar_gz(member: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        body = b"#!/bin/sh\necho fake\n"
        info = tarfile.TarInfo(member)
        info.size = len(body)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


def _zip(member: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, b"MZ fake windows binary")
    return buffer.getvalue()


class NoRemoteInstallScriptTests(unittest.TestCase):
    """The preflight used to ``curl .../opengrep/main/install.sh | bash``."""

    def test_no_shell_script_pipes_a_remote_script_into_a_shell(self) -> None:
        pattern = re.compile(r"curl[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b")
        scripts = sorted([*ROOT.joinpath("scripts").rglob("*.sh"), *ROOT.joinpath("bin").glob("*.sh")])
        self.assertTrue(scripts)
        offenders = [str(p.relative_to(ROOT)) for p in scripts if pattern.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(offenders, [])

    def test_pins_file_is_absolute_before_preflight_changes_directory(self) -> None:
        """Review (#322): digests are read lazily, after `cd "$REPO_DIR"`."""
        lines = (ROOT / "scripts" / "quality" / "preflight.sh").read_text(encoding="utf-8").splitlines()
        absolutize = 'PINS_FILE="$(cd "$(dirname "$PINS_FILE")" && pwd)/$(basename "$PINS_FILE")"'
        self.assertIn(absolutize, lines)
        first_cd = next(i for i, line in enumerate(lines) if line.startswith('cd "$REPO_DIR"'))
        self.assertLess(lines.index(absolutize), first_cd)


class PinFileTests(unittest.TestCase):
    """Every asset the preflight can fetch has a pinned digest."""

    def test_every_preflight_asset_key_is_pinned_with_a_sha256(self) -> None:
        digests = yaml.safe_load(PINS.read_text(encoding="utf-8"))["asset_sha256"]
        expected = {
            "gitleaks_linux_x64",
            "gitleaks_linux_arm64",
            "gitleaks_darwin_x64",
            "gitleaks_darwin_arm64",
            "gitleaks_windows_x64",
            "osv-scanner_linux_amd64",
            "osv-scanner_linux_arm64",
            "osv-scanner_darwin_amd64",
            "osv-scanner_darwin_arm64",
            "osv-scanner_windows_amd64",
            "opengrep_manylinux_x86",
            "opengrep_manylinux_aarch64",
            "opengrep_musllinux_x86",
            "opengrep_musllinux_aarch64",
            "opengrep_osx_x86",
            "opengrep_osx_arm64",
        }
        self.assertEqual(set(digests), expected)
        for key, value in digests.items():
            self.assertRegex(value, r"^[0-9a-f]{64}$", key)


class ProvisioningTests(unittest.TestCase):
    """Source the helper and drive each resolver across platforms."""

    def setUp(self) -> None:
        self.bash = find_bash()
        if self.bash is None:  # pragma: no cover - environment guard
            self.skipTest("no real bash found; cannot source preflight_tools.sh")
        self.workdir = Path(tempfile.mkdtemp(prefix="qzp-preflight-"))
        self.addCleanup(shutil.rmtree, self.workdir, True)
        shutil.copy(HELPER, self.workdir / "preflight_tools.sh")

    def _run(
        self,
        resolver: str,
        *,
        os_name: str,
        arch: str,
        payload: bytes = b"",
        pins: Optional[Dict[str, str]] = None,
        extra: str = "",
        env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        (self.workdir / "payload").write_bytes(payload)
        pin_lines = ["version: 1", "asset_sha256:"] + [f'  {k}: "{v}"' for k, v in (pins or {}).items()]
        (self.workdir / "pins.yml").write_text("\n".join(pin_lines) + "\n", encoding="utf-8", newline="\n")
        script = "\n".join(
            [
                FAKE_CURL,
                ". ./preflight_tools.sh",
                f"os={os_name}; arch={arch}",
                "CACHE_DIR=./cache; PINS_FILE=./pins.yml",
                "PIN_GITLEAKS=8.30.1; PIN_OSV=v2.3.8; PIN_OPENGREP=v1.22.0",
                extra,
                f"{resolver}",
            ]
        )
        overrides = {"QZP_NO_INSTALL": "0", "QZP_PREFLIGHT_PINNED_ONLY": "1", "QZP_LIBC": "glibc"}
        overrides.update(env or {})
        assert self.bash is not None
        return run_step_script(
            script, workdir=self.workdir, bash=self.bash, filename="drive.sh", env_overrides=overrides
        )

    def _urls(self) -> List[str]:
        log = self.workdir / "curl.log"
        return log.read_text(encoding="utf-8").split() if log.exists() else []

    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    # ---- gitleaks -------------------------------------------------------------

    def test_gitleaks_x86_64_linux_uses_the_x64_asset(self) -> None:
        """Regression: the asset is ``linux_x64``; ``linux_amd64`` does not exist."""
        payload = _tar_gz("gitleaks")
        result = self._run(
            "resolve_gitleaks",
            os_name="linux",
            arch="amd64",
            payload=payload,
            pins={"gitleaks_linux_x64": self._digest(payload)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._urls(),
            ["https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz"],
        )
        self.assertEqual(result.stdout.strip(), "./cache/gitleaks/8.30.1/gitleaks")
        self.assertTrue((self.workdir / "cache" / "gitleaks" / "8.30.1" / "gitleaks").is_file())

    def test_gitleaks_arm64_macos_uses_its_own_asset_and_digest(self) -> None:
        payload = _tar_gz("gitleaks")
        result = self._run(
            "resolve_gitleaks",
            os_name="darwin",
            arch="arm64",
            payload=payload,
            pins={"gitleaks_darwin_arm64": self._digest(payload)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self._urls()[0].endswith("/gitleaks_8.30.1_darwin_arm64.tar.gz"))

    def test_gitleaks_windows_uses_the_verified_zip(self) -> None:
        payload = _zip("gitleaks.exe")
        result = self._run(
            "resolve_gitleaks",
            os_name="windows",
            arch="amd64",
            payload=payload,
            pins={"gitleaks_windows_x64": self._digest(payload)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self._urls()[0].endswith("/gitleaks_8.30.1_windows_x64.zip"))
        self.assertTrue((self.workdir / "cache" / "gitleaks" / "8.30.1" / "gitleaks.exe").is_file())

    def test_digest_mismatch_installs_nothing(self) -> None:
        payload = _tar_gz("gitleaks")
        result = self._run(
            "resolve_gitleaks", os_name="linux", arch="amd64", payload=payload, pins={"gitleaks_linux_x64": "0" * 64}
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("SHA-256 mismatch", result.stderr)
        cache = self.workdir / "cache" / "gitleaks" / "8.30.1"
        self.assertEqual(sorted(p.name for p in cache.iterdir()), [])  # no binary, no leftover download

    def test_missing_digest_refuses_to_download(self) -> None:
        result = self._run("resolve_gitleaks", os_name="linux", arch="amd64", payload=_tar_gz("gitleaks"), pins={})
        self.assertEqual(result.returncode, 1)
        self.assertIn("no pinned SHA-256 for 'gitleaks_linux_x64'", result.stderr)
        self.assertEqual(self._urls(), [])

    def test_http_error_installs_nothing(self) -> None:
        payload = _tar_gz("gitleaks")
        result = self._run(
            "resolve_gitleaks",
            os_name="linux",
            arch="amd64",
            payload=payload,
            pins={"gitleaks_linux_x64": self._digest(payload)},
            env={"FAKE_HTTP_ERROR": "1"},
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.workdir / "cache" / "gitleaks" / "8.30.1" / "gitleaks").exists())

    def test_unsupported_platforms_and_no_install_never_download(self) -> None:
        cases = [
            ("resolve_gitleaks", "windows", "arm64", {}),
            ("resolve_gitleaks", "unknown", "amd64", {}),
            ("resolve_gitleaks", "linux", "unknown", {}),
            ("resolve_osv", "windows", "arm64", {}),
            ("resolve_osv", "linux", "unknown", {}),
            ("resolve_osv", "unknown", "amd64", {}),
            ("resolve_opengrep", "windows", "amd64", {}),
            ("resolve_gitleaks", "linux", "amd64", {"QZP_NO_INSTALL": "1"}),
            ("resolve_osv", "linux", "amd64", {"QZP_NO_INSTALL": "1"}),
            ("resolve_opengrep", "linux", "amd64", {"QZP_NO_INSTALL": "1"}),
        ]
        for resolver, os_name, arch, env in cases:
            with self.subTest(resolver=resolver, os=os_name, arch=arch, env=env):
                result = self._run(resolver, os_name=os_name, arch=arch, env=env)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual(self._urls(), [])

    # ---- osv-scanner ----------------------------------------------------------

    def test_osv_scanner_assets_per_platform(self) -> None:
        payload = b"#!/bin/sh\necho osv\n"
        cases = [
            ("linux", "arm64", "osv-scanner_linux_arm64", "osv-scanner_linux_arm64", "osv-scanner"),
            ("darwin", "amd64", "osv-scanner_darwin_amd64", "osv-scanner_darwin_amd64", "osv-scanner"),
            ("windows", "amd64", "osv-scanner_windows_amd64.exe", "osv-scanner_windows_amd64", "osv-scanner.exe"),
        ]
        for os_name, arch, asset, key, binary in cases:
            with self.subTest(os=os_name, arch=arch):
                shutil.rmtree(self.workdir / "cache", ignore_errors=True)
                (self.workdir / "curl.log").unlink(missing_ok=True)
                result = self._run(
                    "resolve_osv", os_name=os_name, arch=arch, payload=payload, pins={key: self._digest(payload)}
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    self._urls(), [f"https://github.com/google/osv-scanner/releases/download/v2.3.8/{asset}"]
                )
                self.assertTrue((self.workdir / "cache" / "osv-scanner" / "v2.3.8" / binary).is_file())

    # ---- opengrep -------------------------------------------------------------

    def test_opengrep_lands_where_the_caller_looks(self) -> None:
        """Regression: install.sh wrote under $HOME/.opengrep, the caller looked in the cache."""
        payload = b"#!/bin/sh\necho opengrep\n"
        result = self._run(
            "resolve_opengrep",
            os_name="linux",
            arch="amd64",
            payload=payload,
            pins={"opengrep_manylinux_x86": self._digest(payload)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "./cache/opengrep/v1.22.0/bin/opengrep")
        self.assertEqual(
            self._urls(), ["https://github.com/opengrep/opengrep/releases/download/v1.22.0/opengrep_manylinux_x86"]
        )

    def test_opengrep_selects_the_platform_asset(self) -> None:
        payload = b"#!/bin/sh\necho opengrep\n"
        cases = [
            ("linux", "amd64", "musl", "opengrep_musllinux_x86"),
            ("linux", "arm64", "glibc", "opengrep_manylinux_aarch64"),
            ("linux", "arm64", "musl", "opengrep_musllinux_aarch64"),
            ("darwin", "amd64", "glibc", "opengrep_osx_x86"),
            ("darwin", "arm64", "glibc", "opengrep_osx_arm64"),
        ]
        for os_name, arch, libc, asset in cases:
            with self.subTest(os=os_name, arch=arch, libc=libc):
                shutil.rmtree(self.workdir / "cache", ignore_errors=True)
                (self.workdir / "curl.log").unlink(missing_ok=True)
                result = self._run(
                    "resolve_opengrep",
                    os_name=os_name,
                    arch=arch,
                    payload=payload,
                    pins={asset: self._digest(payload)},
                    env={"QZP_LIBC": libc},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(self._urls()[0].endswith(f"/v1.22.0/{asset}"))

    # ---- caching and PATH -----------------------------------------------------

    def test_a_cached_binary_that_still_verifies_is_reused_without_download(self) -> None:
        content = b"#!/bin/sh\necho genuine osv\n"
        cached = self.workdir / "cache" / "osv-scanner" / "v2.3.8" / "osv-scanner"
        cached.parent.mkdir(parents=True)
        cached.write_bytes(content)
        result = self._run(
            "resolve_osv", os_name="linux", arch="amd64", pins={"osv-scanner_linux_amd64": self._digest(content)}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._urls(), [])
        self.assertEqual(cached.read_bytes(), content)

    def test_a_cached_binary_that_no_longer_verifies_is_refetched(self) -> None:
        """Review (#322): a stale/replaced cache must not bypass the digest check."""
        genuine = b"#!/bin/sh\necho genuine osv\n"
        cached = self.workdir / "cache" / "osv-scanner" / "v2.3.8" / "osv-scanner"
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"#!/bin/sh\necho replaced\n")  # e.g. left by the old unverified downloader
        result = self._run(
            "resolve_osv",
            os_name="linux",
            arch="amd64",
            payload=genuine,
            pins={"osv-scanner_linux_amd64": self._digest(genuine)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self._urls()), 1)
        self.assertEqual(cached.read_bytes(), genuine)

    def test_gitleaks_is_reextracted_from_its_verified_cached_archive(self) -> None:
        """The digest pins the archive, so the binary is always re-derived from it."""
        archive = _tar_gz("gitleaks")
        cache = self.workdir / "cache" / "gitleaks" / "8.30.1"
        cache.mkdir(parents=True)
        (cache / "gitleaks.tar.gz").write_bytes(archive)
        (cache / "gitleaks").write_bytes(b"#!/bin/sh\necho replaced binary\n")
        result = self._run(
            "resolve_gitleaks", os_name="linux", arch="amd64", pins={"gitleaks_linux_x64": self._digest(archive)}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._urls(), [])
        self.assertEqual((cache / "gitleaks").read_bytes(), b"#!/bin/sh\necho fake\n")

    def test_a_cached_gitleaks_archive_that_fails_verification_is_refetched(self) -> None:
        archive = _tar_gz("gitleaks")
        cache = self.workdir / "cache" / "gitleaks" / "8.30.1"
        cache.mkdir(parents=True)
        (cache / "gitleaks.tar.gz").write_bytes(b"truncated")
        result = self._run(
            "resolve_gitleaks",
            os_name="linux",
            arch="amd64",
            payload=archive,
            pins={"gitleaks_linux_x64": self._digest(archive)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self._urls()), 1)
        self.assertEqual((cache / "gitleaks.tar.gz").read_bytes(), archive)

    def test_a_tool_on_path_is_used_unless_pinned_only(self) -> None:
        stub = "gitleaks() { echo 8.30.1; }; export -f gitleaks"
        result = self._run(
            "resolve_gitleaks", os_name="linux", arch="amd64", extra=stub, env={"QZP_PREFLIGHT_PINNED_ONLY": "0"}
        )
        self.assertEqual((result.returncode, result.stdout.strip(), self._urls()), (0, "gitleaks", []))
        result = self._run("resolve_gitleaks", os_name="linux", arch="amd64", extra=stub, pins={})
        self.assertEqual(result.returncode, 1)  # pinned-only: the PATH tool is ignored, no digest -> refused
        self.assertIn("no pinned SHA-256", result.stderr)

    def test_a_tool_on_path_at_exactly_the_pin_is_used(self) -> None:
        """Real version-output shapes: a word of any line, with or without a leading v."""
        cases = [
            ("resolve_osv", "osv-scanner() { echo 'osv-scanner version: 2.3.8'; echo 'commit: abc'; }", "osv-scanner"),
            ("resolve_gitleaks", "gitleaks() { echo v8.30.1; }; export -f gitleaks", "gitleaks"),
        ]
        for resolver, stub, expected in cases:
            with self.subTest(resolver=resolver):
                (self.workdir / "curl.log").unlink(missing_ok=True)
                result = self._run(
                    resolver, os_name="linux", arch="amd64", extra=stub, env={"QZP_PREFLIGHT_PINNED_ONLY": "0"}
                )
                self.assertEqual((result.returncode, result.stdout.strip(), self._urls()), (0, expected, []))

    def test_a_tool_on_path_at_another_version_is_not_used(self) -> None:
        """A previously pinned engine on PATH must not run under the new pin's name."""
        cases = [
            ("resolve_gitleaks", "gitleaks() { echo 8.29.0; }; export -f gitleaks", "gitleaks_linux_x64", True),
            ("resolve_osv", "osv-scanner() { echo 'osv-scanner version: 2.3.7'; }", "osv-scanner_linux_amd64", False),
            (
                "resolve_opengrep",
                "opengrep() { echo 1.22.0-rc1; }; export -f opengrep",
                "opengrep_manylinux_x86",
                False,
            ),
        ]
        for resolver, stub, asset, archived in cases:
            with self.subTest(resolver=resolver):
                shutil.rmtree(self.workdir / "cache", ignore_errors=True)
                (self.workdir / "curl.log").unlink(missing_ok=True)
                payload = _tar_gz("gitleaks") if archived else b"#!/bin/sh\necho pinned\n"
                result = self._run(
                    resolver,
                    os_name="linux",
                    arch="amd64",
                    payload=payload,
                    extra=stub,
                    pins={asset: self._digest(payload)},
                    env={"QZP_PREFLIGHT_PINNED_ONLY": "0"},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.strip().startswith("./cache/"), result.stdout)
                self.assertEqual(len(self._urls()), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
