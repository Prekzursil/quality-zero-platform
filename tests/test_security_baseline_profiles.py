from __future__ import absolute_import

import unittest

import yaml

from scripts.quality.control_plane import active_required_contexts, load_inventory, load_repo_profile
from scripts.quality.render_repo_baseline import (
    render_codeql_wrapper,
    render_dependabot_config,
    render_security_policy,
)
from tests.control_plane_support import ROOT


class SecurityBaselineProfileTests(unittest.TestCase):
    """Protect the managed CodeQL, Dependabot, and SECURITY baseline contract."""

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
            platform_release_sha="0123456789abcdef0123456789abcdef01234567"
        )
        self.assertIn(
            "Prekzursil/quality-zero-platform/.github/workflows/reusable-codeql.yml@0123456789abcdef0123456789abcdef01234567",
            rendered,
        )
        self.assertIn("merge_group:", rendered)
        self.assertIn('cron: "23 3 * * 1"', rendered)

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
