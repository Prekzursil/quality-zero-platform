from __future__ import absolute_import

import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.quality.control_plane import active_required_contexts, load_inventory, load_repo_profile
from scripts.quality.render_repo_baseline import (
    LEGACY_ZERO_WORKFLOW_FILES,
    render_codeql_wrapper,
    render_dependabot_config,
    render_qlty_config,
    render_repo_baseline,
    render_security_policy,
)
from tests.control_plane_support import ROOT


class SecurityBaselineProfileTests(unittest.TestCase):
    """Protect the managed CodeQL, Dependabot, SECURITY, and QLTY baseline contract."""

    def test_all_governed_repos_declare_codeql_and_dependabot(self) -> None:
        """Every enrolled repo should expose the managed security-baseline metadata."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")

        for entry in inventory["repos"]:
            profile = load_repo_profile(inventory, entry["slug"])
            self.assertTrue(profile["codeql"]["enabled"], entry["slug"])
            self.assertTrue(profile["codeql"]["languages"], entry["slug"])
            self.assertTrue(profile["dependabot"]["enabled"], entry["slug"])
            self.assertIn(
                "codeql / CodeQL",
                active_required_contexts(profile, event_name="push"),
            )
            self.assertIn(
                "codeql / CodeQL",
                active_required_contexts(profile, event_name="ruleset"),
            )
            self.assertIn("codeql / CodeQL", profile["required_contexts"]["target"])

    def test_render_dependabot_config_includes_github_actions_and_repo_updates(self) -> None:
        """Dependabot rendering should include repo ecosystems plus github-actions."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/Reframe")

        rendered = yaml.safe_load(render_dependabot_config(profile))
        updates = rendered["updates"]
        pairs = {(item["package-ecosystem"], item["directory"]) for item in updates}

        self.assertIn(("github-actions", "/"), pairs)
        self.assertIn(("npm", "/apps/web"), pairs)
        self.assertIn(("npm", "/apps/desktop"), pairs)
        self.assertIn(("cargo", "/apps/desktop/src-tauri"), pairs)
        self.assertIn(("pip", "/apps/api"), pairs)

    def test_render_codeql_wrapper_pins_requested_controller_sha(self) -> None:
        """Repo CodeQL wrappers must use immutable controller refs."""
        rendered = render_codeql_wrapper(
            repo_slug="Prekzursil/WebCoder",
            platform_release_sha="0123456789abcdef0123456789abcdef01234567"
        )
        self.assertIn(
            "Prekzursil/quality-zero-platform/.github/workflows/reusable-codeql.yml@0123456789abcdef0123456789abcdef01234567",
            rendered,
        )
        self.assertIn("merge_group:", rendered)
        self.assertIn('cron: "23 3 * * 1"', rendered)

    def test_render_codeql_wrapper_uses_local_reusable_for_controller_repo(self) -> None:
        """The controller repo should use its local reusable workflow and current ref."""
        rendered = render_codeql_wrapper(
            repo_slug="Prekzursil/quality-zero-platform",
            platform_release_sha="0123456789abcdef0123456789abcdef01234567",
        )
        self.assertIn("uses: ./.github/workflows/reusable-codeql.yml", rendered)
        self.assertIn("platform_repository: ${{ github.repository }}", rendered)
        self.assertIn(
            "platform_ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            rendered,
        )

    def test_render_security_policy_uses_repo_advisory_url(self) -> None:
        """SECURITY.md should point at the repo's private advisory entrypoint."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/WebCoder")

        rendered = render_security_policy(profile)
        self.assertIn(
            "<https://github.com/Prekzursil/WebCoder/security/advisories/new>",
            rendered,
        )
        self.assertIn("@Prekzursil", rendered)

    def test_render_qlty_config_blocks_smells_for_governed_repos(self) -> None:
        """Managed QLTY config should be the same minimal block-mode baseline."""
        rendered = render_qlty_config()
        self.assertIn('config_version = "0"', rendered)
        self.assertIn('name = "default"', rendered)
        self.assertIn('mode = "block"', rendered)

    def test_render_repo_baseline_removes_legacy_zero_workflows(self) -> None:
        """Baseline rendering should delete superseded repo-local zero workflows."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/pbinfo-get-unsolved")

        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            workflows = repo_root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            for filename in LEGACY_ZERO_WORKFLOW_FILES:
                (workflows / filename).write_text("name: legacy\n", encoding="utf-8")

            render_repo_baseline(
                profile=profile,
                repo_root=repo_root,
                platform_release_sha="0123456789abcdef0123456789abcdef01234567",
            )

            for filename in LEGACY_ZERO_WORKFLOW_FILES:
                self.assertFalse((workflows / filename).exists(), filename)
            self.assertTrue((repo_root / ".qlty" / "qlty.toml").exists())
