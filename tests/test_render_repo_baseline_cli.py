"""Tests for the ``render_repo_baseline`` CLI and the branches the profile tests never take.

``tests/test_security_baseline_profiles.py`` pins the rendered content; these
pin the command (argument parsing, the default ``main`` release ref, the real
inventory), a declared github-actions update, a repo with no legacy zero-gate
workflows, and a profile with qlty disabled.
"""

from __future__ import absolute_import

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml  # type: ignore[import-untyped]

from scripts.quality import render_repo_baseline as baseline


class RenderRepoBaselineCliTests(unittest.TestCase):
    """`python render_repo_baseline.py --repo-slug ... --repo-root ...` renders the managed files."""

    def setUp(self) -> None:
        """A scratch checkout to render into."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)

    def test_main_renders_into_the_checkout_with_main_as_the_default_ref(self) -> None:
        """A blank release SHA falls back to `main`; codeql.yml, dependabot.yml and SECURITY.md appear."""
        argv = [
            "render_repo_baseline.py",
            "--repo-slug",
            "Prekzursil/event-link",
            "--repo-root",
            str(self.repo),
            "--platform-release-sha",
            "   ",
        ]
        with patch.object(sys, "argv", argv):
            self.assertEqual(baseline.main(), 0)
        codeql = (self.repo / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8")
        self.assertIn("reusable-codeql.yml@main", codeql)
        self.assertTrue((self.repo / ".github" / "dependabot.yml").is_file())
        self.assertIn(
            "Prekzursil/event-link/security/advisories/new", (self.repo / "SECURITY.md").read_text(encoding="utf-8")
        )

    def test_a_declared_github_actions_update_is_not_duplicated(self) -> None:
        """The managed github-actions entry is added only when the profile does not declare one."""
        profile = {
            "dependabot": {
                "updates": [{"ecosystem": "github-actions", "directory": "/"}, {"ecosystem": "pip", "directory": "/"}],
                "schedule_interval": "weekly",
                "open_pull_requests_limit": 5,
                "labels": [],
            }
        }
        rendered = yaml.safe_load(baseline.render_dependabot_config(profile))
        ecosystems = [entry["package-ecosystem"] for entry in rendered["updates"]]
        self.assertEqual(sorted(ecosystems), ["github-actions", "pip"])

    def test_a_qlty_free_repo_without_legacy_workflows(self) -> None:
        """Nothing to delete is not an error, and no qlty.toml is written when qlty is off."""
        profile = {
            "slug": "o/r",
            "dependabot": {"updates": [], "schedule_interval": "weekly", "open_pull_requests_limit": 5, "labels": []},
            "enabled_scanners": {"qlty": False},
        }
        (self.repo / ".github" / "workflows").mkdir(parents=True)
        (self.repo / ".github" / "workflows" / "sonar-zero.yml").write_text("legacy", encoding="utf-8")
        baseline.render_repo_baseline(profile=profile, repo_root=self.repo, platform_release_sha="0" * 40)
        self.assertFalse((self.repo / ".github" / "workflows" / "sonar-zero.yml").exists())
        self.assertFalse((self.repo / ".qlty").exists())
        self.assertTrue((self.repo / "SECURITY.md").is_file())

    def test_loading_by_path_bootstraps_the_repo_root(self) -> None:
        """Run by path from a foreign cwd, the module puts the repo root on sys.path exactly once."""
        module_path = Path(baseline.__file__).resolve()
        repo_root = str(module_path.parents[2])
        for root_on_path in (False, True):
            with self.subTest(root_on_path=root_on_path):
                spec = importlib.util.spec_from_file_location("render_repo_baseline_under_test", module_path)
                if spec is None or spec.loader is None:  # pragma: no cover - environment guard
                    self.fail("no module spec for render_repo_baseline")
                module = importlib.util.module_from_spec(spec)
                original = list(sys.path)
                try:
                    sys.path[:] = [entry for entry in sys.path if entry != repo_root]
                    if root_on_path:
                        sys.path.insert(0, repo_root)
                    spec.loader.exec_module(module)
                    self.assertEqual(sys.path.count(repo_root), 1)
                finally:
                    sys.path[:] = original


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
