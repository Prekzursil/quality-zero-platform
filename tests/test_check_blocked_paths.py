"""Unit coverage for ``scripts.quality.check_blocked_paths``.

The blocked-paths guard is the single source of truth that keeps the autofix
sweep + verifier from touching sensitive infrastructure files. These tests
exercise the pure predicates directly and drive the CLI through real temporary
git repositories (no ``subprocess`` mocking for the happy paths), covering the
exit 0 / 1 / 2 arcs plus the git-error fail-loud path.
"""

from __future__ import absolute_import

import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import List, Sequence

from scripts.quality import check_blocked_paths as cbp


def _git(args: Sequence[str], cwd: Path) -> None:
    """Run a git command in ``cwd``, failing loud on a nonzero exit."""
    subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        check=True,
    )


class IsBlockedPathTests(unittest.TestCase):
    """``is_blocked_path`` matches the canonical patterns at any depth."""

    def test_github_dir_shallow(self) -> None:
        """A direct ``.github`` child is blocked."""
        self.assertTrue(cbp.is_blocked_path(".github/dependabot.yml"))

    def test_github_dir_deep(self) -> None:
        """``.github/**`` matches nested paths at three+ levels deep."""
        self.assertTrue(
            cbp.is_blocked_path(".github/workflows/ci/reusable.yml")
        )

    def test_backslash_separators_normalized(self) -> None:
        """Windows separators normalize to POSIX before matching."""
        self.assertTrue(cbp.is_blocked_path(r".github\workflows\ci.yml"))

    def test_dot_slash_prefix_stripped(self) -> None:
        """A leading ``./`` prefix is removed before matching."""
        self.assertTrue(cbp.is_blocked_path("./pyproject.toml"))

    def test_dockerfile_exact(self) -> None:
        """A bare ``Dockerfile`` is blocked."""
        self.assertTrue(cbp.is_blocked_path("Dockerfile"))

    def test_dockerfile_suffixed(self) -> None:
        """``Dockerfile.*`` variants are blocked."""
        self.assertTrue(cbp.is_blocked_path("Dockerfile.prod"))

    def test_docker_compose_glob(self) -> None:
        """``docker-compose*.yml`` variants are blocked."""
        self.assertTrue(cbp.is_blocked_path("docker-compose.override.yml"))

    def test_requirements_glob(self) -> None:
        """``requirements*.txt`` variants are blocked."""
        self.assertTrue(cbp.is_blocked_path("requirements-dev.txt"))

    def test_setup_cfg(self) -> None:
        """``setup.cfg`` is blocked."""
        self.assertTrue(cbp.is_blocked_path("setup.cfg"))

    def test_npmrc(self) -> None:
        """``.npmrc`` is blocked."""
        self.assertTrue(cbp.is_blocked_path(".npmrc"))

    def test_pip_conf_nested_slash_pattern(self) -> None:
        """``.pip/pip.conf`` (a slash pattern) is blocked on the full path."""
        self.assertTrue(cbp.is_blocked_path(".pip/pip.conf"))

    def test_lockfile_depth_independent(self) -> None:
        """Lockfiles are blocked wherever they live (basename leg)."""
        self.assertTrue(cbp.is_blocked_path("crates/inner/Cargo.lock"))
        self.assertTrue(cbp.is_blocked_path("frontend/package-lock.json"))
        self.assertTrue(cbp.is_blocked_path("svc/poetry.lock"))

    def test_gitattributes_glob(self) -> None:
        """``*.gitattributes`` (and the bare file) is blocked."""
        self.assertTrue(cbp.is_blocked_path(".gitattributes"))

    def test_windows_scripts(self) -> None:
        """``*.bat`` and ``*.ps1`` are blocked."""
        self.assertTrue(cbp.is_blocked_path("tools/install.bat"))
        self.assertTrue(cbp.is_blocked_path("scripts/deploy.ps1"))

    def test_benign_path_not_blocked(self) -> None:
        """A normal source file is not blocked."""
        self.assertFalse(cbp.is_blocked_path("src/app/main.py"))

    def test_empty_path_not_blocked(self) -> None:
        """An empty / whitespace-only path is not blocked."""
        self.assertFalse(cbp.is_blocked_path("   "))


class BlockedPathsTests(unittest.TestCase):
    """``blocked_paths`` returns only the offending subset, in order."""

    def test_returns_blocked_subset_in_order(self) -> None:
        """Only blocked entries are returned, preserving input order."""
        paths = [
            "src/main.py",
            ".github/workflows/ci.yml",
            "README.md",
            "Cargo.lock",
        ]
        self.assertEqual(
            cbp.blocked_paths(paths),
            [".github/workflows/ci.yml", "Cargo.lock"],
        )

    def test_all_benign_returns_empty(self) -> None:
        """A fully benign list yields an empty result."""
        self.assertEqual(cbp.blocked_paths(["a.py", "b.ts"]), [])


class _GitRepoTestCase(unittest.TestCase):
    """Base case providing an initialised temp git repo with one commit."""

    def setUp(self) -> None:
        """Create a temp git repo with a benign committed baseline."""
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self._tmpdir.name)
        _git(["init"], self.repo_dir)
        _git(["config", "user.email", "test@test.com"], self.repo_dir)
        _git(["config", "user.name", "Test"], self.repo_dir)
        (self.repo_dir / "keep.txt").write_text("seed\n", encoding="utf-8")
        _git(["add", "."], self.repo_dir)
        _git(["commit", "-m", "init"], self.repo_dir)

    def tearDown(self) -> None:
        """Remove the temp repo."""
        self._tmpdir.cleanup()


class ChangedFilesTests(_GitRepoTestCase):
    """``_changed_files`` mirrors the working-tree diff against a ref."""

    def test_lists_changed_files(self) -> None:
        """A new uncommitted file shows up in the diff list."""
        (self.repo_dir / "extra.py").write_text("x = 1\n", encoding="utf-8")
        _git(["add", "."], self.repo_dir)
        changed = cbp._changed_files("HEAD", str(self.repo_dir))
        self.assertIn("extra.py", changed)

    def test_no_changes_returns_empty(self) -> None:
        """A clean tree yields an empty change list (no phantom entries)."""
        self.assertEqual(cbp._changed_files("HEAD", str(self.repo_dir)), [])

    def test_bad_ref_raises(self) -> None:
        """An unknown ref makes git exit nonzero → fail loud."""
        with self.assertRaises(cbp.BlockedPathsError):
            cbp._changed_files("no-such-ref", str(self.repo_dir))


class MainBaseRefTests(_GitRepoTestCase):
    """``main`` over the ``--base-ref`` arc covers exits 0, 1, and 2."""

    def _run(self, *extra: str) -> int:
        """Invoke ``main`` with ``--base-ref HEAD`` plus the repo dir."""
        argv: List[str] = [
            "--base-ref",
            "HEAD",
            "--repo-dir",
            str(self.repo_dir),
            *extra,
        ]
        return cbp.main(argv)

    def test_clean_change_exits_zero(self) -> None:
        """A benign uncommitted change exits 0."""
        (self.repo_dir / "ok.py").write_text("y = 2\n", encoding="utf-8")
        _git(["add", "."], self.repo_dir)
        self.assertEqual(self._run(), 0)

    def test_blocked_change_exits_one(self) -> None:
        """A change to ``.github/**`` exits 1 and prints the offender."""
        workflows = self.repo_dir / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")
        _git(["add", "."], self.repo_dir)
        self.assertEqual(self._run(), 1)

    def test_bad_ref_exits_two(self) -> None:
        """An unknown ref reports a git error and exits 2."""
        rc = cbp.main(
            ["--base-ref", "no-such-ref", "--repo-dir", str(self.repo_dir)]
        )
        self.assertEqual(rc, 2)


class MainPathsTests(unittest.TestCase):
    """``main`` over the ``--paths`` arc covers exits 0 and 1."""

    def test_clean_paths_exit_zero(self) -> None:
        """Explicit benign paths exit 0."""
        self.assertEqual(cbp.main(["--paths", "src/a.py", "src/b.py"]), 0)

    def test_blocked_paths_exit_one(self) -> None:
        """An explicit blocked path exits 1."""
        self.assertEqual(
            cbp.main(["--paths", "src/a.py", "pyproject.toml"]), 1
        )


class MainUsageTests(unittest.TestCase):
    """Argparse enforces exactly one source selector (usage → exit 2)."""

    def test_no_source_exits_two(self) -> None:
        """Neither --base-ref nor --paths → argparse SystemExit(2)."""
        with self.assertRaises(SystemExit) as ctx:
            cbp.main([])
        self.assertEqual(ctx.exception.code, 2)

    def test_both_sources_exit_two(self) -> None:
        """Supplying both selectors → argparse SystemExit(2)."""
        with self.assertRaises(SystemExit) as ctx:
            cbp.main(["--base-ref", "HEAD", "--paths", "x.py"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
