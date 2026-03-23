from __future__ import absolute_import

import tempfile
import unittest
from pathlib import Path

from scripts.quality import control_plane_admin


class ControlPlaneAdminTests(unittest.TestCase):
    def _write_repo(self, root: Path) -> None:
        (root / "inventory").mkdir(parents=True, exist_ok=True)
        (root / "profiles" / "repos").mkdir(parents=True, exist_ok=True)
        (root / "inventory" / "repos.yml").write_text("version: 1\nrepos: []\n", encoding="utf-8")

    def test_enroll_repo_appends_inventory_entry_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_repo(root)

            control_plane_admin.enroll_repo(
                repo_root=root,
                repo_slug="Prekzursil/example-repo",
                profile_id="example-repo",
                stack="python-web",
                rollout="phase2-wave0",
                default_branch="main",
            )

            inventory = (root / "inventory" / "repos.yml").read_text(encoding="utf-8")
            profile = (root / "profiles" / "repos" / "example-repo.yml").read_text(encoding="utf-8")

        self.assertIn("slug: Prekzursil/example-repo", inventory)
        self.assertIn("profile: example-repo", inventory)
        self.assertIn("stack: python-web", profile)
        self.assertIn("slug: Prekzursil/example-repo", profile)

    def test_set_scanner_issue_policy_and_coverage_mode_mutate_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_repo(root)
            profile_path = root / "profiles" / "repos" / "example-repo.yml"
            profile_path.write_text(
                "slug: Prekzursil/example-repo\n"
                "stack: python-web\n"
                "enabled_scanners:\n"
                "  sonar: true\n"
                "coverage:\n"
                "  assert_mode:\n"
                "    default: enforce\n",
                encoding="utf-8",
            )

            control_plane_admin.set_scanner(repo_root=root, profile_id="example-repo", scanner="sonar", enabled=False)
            control_plane_admin.set_issue_policy(
                repo_root=root,
                profile_id="example-repo",
                mode="ratchet",
                baseline_ref="main",
            )
            control_plane_admin.set_coverage_mode(
                repo_root=root,
                profile_id="example-repo",
                event_name="pull_request",
                mode="non_regression",
            )

            profile_text = profile_path.read_text(encoding="utf-8")

        self.assertIn("sonar: false", profile_text)
        self.assertIn("issue_policy:", profile_text)
        self.assertIn("mode: ratchet", profile_text)
        self.assertIn("baseline_ref: main", profile_text)
        self.assertIn("pull_request: non_regression", profile_text)

    def test_set_required_context_adds_and_removes_contexts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_repo(root)
            profile_path = root / "profiles" / "repos" / "example-repo.yml"
            profile_path.write_text(
                "slug: Prekzursil/example-repo\n"
                "stack: python-web\n"
                "required_contexts:\n"
                "  target:\n"
                "    - Coverage 100 Gate\n",
                encoding="utf-8",
            )

            control_plane_admin.set_required_context(
                repo_root=root,
                profile_id="example-repo",
                context_set="pull_request_only",
                context_name="qlty coverage diff",
                present=True,
            )
            control_plane_admin.set_required_context(
                repo_root=root,
                profile_id="example-repo",
                context_set="target",
                context_name="Coverage 100 Gate",
                present=False,
            )

            profile_text = profile_path.read_text(encoding="utf-8")

        self.assertIn("pull_request_only:", profile_text)
        self.assertIn("qlty coverage diff", profile_text)
        self.assertNotIn("    - Coverage 100 Gate", profile_text)
