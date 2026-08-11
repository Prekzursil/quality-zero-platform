"""Guard the working tree against generated lane artifacts.

Background: ``coverage-100/``, ``deps-zero/``, ``quality-rollup/`` and
``deepsource-visible-zero/`` hold lane reports that the scanner matrix produces
at CI time and uploads as workflow artifacts. Nothing reads a committed copy --
``build_quality_rollup.load_lane_payloads`` reads them from the DOWNLOADED
artifact tree and ``run_coverage_gate._load_baseline_coverage_payload`` reads
``coverage-100/coverage.json`` out of a downloaded artifact ZIP.

Eight such files (plus an empty ``run.json``) were nonetheless committed in
``ec6e80a`` -- swept in by a local ``bash scripts/verify`` run whose test suite
wrote them into the repo root. Every later local run rewrote them with nothing
but a fresh timestamp, so any contributor could commit timestamp-only noise into
an unrelated PR.

These tests pin both halves of the fix: the paths stay untracked and ignored,
and the test suite no longer writes into the repository root.
"""

from __future__ import absolute_import

import shutil
import subprocess
import unittest
from pathlib import Path
from typing import Dict, List, Optional

from tests.script_entrypoint_support import assert_in_process_entrypoint_failure
from tests.workspace_isolation import REPO_ROOT, isolated_cwd

# Directories whose contents are produced by a lane writer's default output path.
GENERATED_ARTIFACT_DIRS = (
    "coverage-100",
    "deps-zero",
    "quality-rollup",
    "deepsource-visible-zero",
)
# Individual generated files that do not live under a directory of their own.
GENERATED_ARTIFACT_FILES = ("run.json",)
GENERATED_ARTIFACT_PROBES = (
    *(f"{name}/probe.json" for name in GENERATED_ARTIFACT_DIRS),
    *GENERATED_ARTIFACT_FILES,
)

GIT_AVAILABLE = shutil.which("git") is not None and (REPO_ROOT / ".git").exists()


def _run_git_probe(*args: str) -> subprocess.CompletedProcess:
    """Run one read-only git command in the repository root and return the result.

    Deliberately NOT ``scripts.quality.apply_drift_pr._git``: that helper takes an
    injected runner and passes ``check=True``, while these probes must inspect a
    non-zero exit (``git check-ignore`` reports "not ignored" as exit 1).
    """
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _snapshot_repo_root_artifacts() -> Dict[str, Optional[bytes]]:
    """Map every generated-artifact file currently in the repo root to its bytes."""
    snapshot: Dict[str, Optional[bytes]] = {}
    targets: List[Path] = [REPO_ROOT / name for name in GENERATED_ARTIFACT_FILES]
    for directory in GENERATED_ARTIFACT_DIRS:
        root = REPO_ROOT / directory
        if root.is_dir():
            targets.extend(sorted(root.rglob("*")))
        else:
            targets.append(root)
    for target in targets:
        key = target.relative_to(REPO_ROOT).as_posix()
        snapshot[key] = target.read_bytes() if target.is_file() else None
    return snapshot


@unittest.skipUnless(GIT_AVAILABLE, "git checkout required to inspect tracked/ignored state")
class GeneratedArtifactsAreNotVersionedTests(unittest.TestCase):
    """Generated Artifacts Are Not Versioned Tests."""

    def test_git_probe_reaches_this_repository(self) -> None:
        """Control the git detector before trusting any empty result below."""
        tracked = _run_git_probe("ls-files", "--", "scripts/verify")
        self.assertEqual(tracked.returncode, 0, tracked.stderr)
        self.assertEqual(tracked.stdout.strip(), "scripts/verify")

    def test_generated_artifact_paths_are_untracked(self) -> None:
        """No generated lane report may be tracked by git."""
        for path in (*GENERATED_ARTIFACT_DIRS, *GENERATED_ARTIFACT_FILES):
            with self.subTest(path=path):
                tracked = _run_git_probe("ls-files", "--", path)
                self.assertEqual(tracked.returncode, 0, tracked.stderr)
                self.assertEqual(tracked.stdout.strip(), "")

    def test_generated_artifact_paths_are_git_ignored(self) -> None:
        """Every generated lane report path must be ignored, so it cannot be added by accident."""
        for path in GENERATED_ARTIFACT_PROBES:
            with self.subTest(path=path):
                ignored = _run_git_probe("check-ignore", "--", path)
                self.assertEqual(ignored.returncode, 0, f"{path} is not git-ignored: {ignored.stderr}")


class EntrypointTestsDoNotWriteIntoTheRepoRootTests(unittest.TestCase):
    """Entrypoint Tests Do Not Write Into The Repo Root Tests."""

    def test_in_process_entrypoint_helper_leaves_the_repo_root_untouched(self) -> None:
        """Running a lane entrypoint in-process must not create or rewrite repo-root reports."""
        before = _snapshot_repo_root_artifacts()
        assert_in_process_entrypoint_failure(self, "scripts/quality/check_deepsource_zero.py")
        self.assertEqual(_snapshot_repo_root_artifacts(), before)


class IsolatedCwdTests(unittest.TestCase):
    """Isolated Cwd Tests."""

    def test_isolated_cwd_moves_into_a_throwaway_directory_and_restores(self) -> None:
        """Cover isolated cwd moves into a throwaway directory and restores."""
        original = Path.cwd()
        with isolated_cwd() as sandbox:
            self.assertEqual(Path.cwd().resolve(), sandbox)
            self.assertNotEqual(sandbox, original.resolve())
        self.assertEqual(Path.cwd(), original)

    def test_isolated_cwd_restores_the_previous_directory_on_error(self) -> None:
        """Cover isolated cwd restores the previous directory on error."""
        original = Path.cwd()
        with self.assertRaises(RuntimeError), isolated_cwd():
            raise RuntimeError("boom")
        self.assertEqual(Path.cwd(), original)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
