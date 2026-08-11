from __future__ import absolute_import

import tempfile
import unittest
from pathlib import Path

import yaml
from tests.control_plane_support import ROOT

from scripts.quality.control_plane import active_required_contexts, load_inventory, load_repo_profile
from scripts.quality.render_repo_baseline import (
    LEGACY_ZERO_WORKFLOW_FILES,
    render_codeql_wrapper,
    render_dependabot_config,
    render_qlty_config,
    render_repo_baseline,
    render_security_policy,
)


class SecurityBaselineProfileTests(unittest.TestCase):
    """Protect the managed CodeQL, Dependabot, SECURITY, and QLTY baseline contract."""

    @staticmethod
    def _enforces_codeql(contexts) -> bool:
        """Is CodeQL a REQUIRED context, under either of GitHub's two setups?

        There are two supported ways to run CodeQL, and they emit different check
        names:

          * ADVANCED setup -- a `codeql.yml` workflow in the repo -- emits
            ``codeql / CodeQL``.
          * DEFAULT setup -- GitHub-managed, configured in the Security tab -- emits
            one ``analyze (<language>)`` per analysed language and NEVER emits the
            workflow-style name.

        This assertion previously accepted only the advanced spelling, which silently
        wedges any repo migrated to default setup: the required context can never be
        reported, so no PR can ever merge. That is not hypothetical -- Prekzursil/WebCoder
        requires ``codeql / CodeQL`` while emitting only ``Analyze (...)``, and has been
        unmergeable since its CodeQL moved to default setup.

        This is a recognition fix, not a relaxation: a repo that requires NEITHER form
        still fails. Match is case-insensitive because the ruleset context and the
        emitted check-run name differ in capitalisation (``analyze`` vs ``Analyze``).
        """
        lowered = {str(c).lower() for c in contexts}
        if "codeql / codeql" in lowered:
            return True
        return any(c.startswith("analyze (") for c in lowered)

    def test_all_governed_repos_declare_codeql_and_dependabot(self) -> None:
        """Every enrolled repo should expose the managed security-baseline metadata."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")

        for entry in inventory["repos"]:
            profile = load_repo_profile(inventory, entry["slug"])
            self.assertTrue(profile["codeql"]["enabled"], entry["slug"])
            self.assertTrue(profile["codeql"]["languages"], entry["slug"])
            self.assertTrue(profile["dependabot"]["enabled"], entry["slug"])
            for event in ("push", "ruleset"):
                contexts = active_required_contexts(profile, event_name=event)
                self.assertTrue(
                    self._enforces_codeql(contexts),
                    f"{entry['slug']} ({event}): no CodeQL context is required under "
                    f"either setup (advanced 'codeql / CodeQL' or default "
                    f"'analyze (<lang>)'); got {sorted(contexts)}",
                )
            self.assertTrue(
                self._enforces_codeql(profile["required_contexts"]["target"]),
                f"{entry['slug']} (target): no CodeQL context is required under either "
                f"setup; got {sorted(profile['required_contexts']['target'])}",
            )

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
            repo_slug="Prekzursil/WebCoder", platform_release_sha="0123456789abcdef0123456789abcdef01234567"
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
