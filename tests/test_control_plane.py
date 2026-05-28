from __future__ import absolute_import

import unittest

from scripts.quality.control_plane import (
    active_required_contexts,
    build_ruleset_payload,
    load_inventory,
    load_repo_profile,
)
from tests.control_plane_support import ControlPlaneAssertions, ROOT


class ControlPlaneTests(unittest.TestCase, ControlPlaneAssertions):
    """Control-plane regression tests for required contexts and repo contracts."""

    def _assert_quality_zero_platform_contexts(
        self,
        *contexts: str,
        push_contexts,
        ruleset_contexts,
        target_contexts,
        ruleset_status_checks,
    ) -> None:
        """Assert the shared push/ruleset/target contract for one repo."""
        for context in contexts:
            self.assertIn(context, push_contexts)
            self.assertIn(context, target_contexts)
            self.assertIn(context, ruleset_contexts)
            self.assertIn(context, ruleset_status_checks)

    def test_inventory_expands_to_17_repos(self) -> None:
        """Inventory should continue to expose the full enrolled repo set."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        self.assertEqual(len(inventory["repos"]), 17)

    def test_common_phase1_template_contexts_resolve(self) -> None:
        """Phase-1 overlays should keep their shared required-context defaults."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")
        pbinfo = load_repo_profile(inventory, "Prekzursil/pbinfo-get-unsolved")
        pr_contexts = active_required_contexts(profile, event_name="pull_request")
        merge_group_contexts = active_required_contexts(
            profile, event_name="merge_group"
        )

        self.assertEqual(profile["stack"], "node-frontend")
        self.assertEqual(profile["coverage"]["branch_min_percent"], 100.0)
        self.assertEqual(pbinfo["coverage"]["branch_min_percent"], 100.0)
        # STRICT-ZERO, WHOLE-TREE: every native cloud context (Codacy
        # Static Code Analysis, DeepScan, qlty check/coverage/diff) is now
        # enforced on push too, not just on pull_request.
        self.assertEqual(
            active_required_contexts(profile, event_name="push"),
            [
                "shared-scanner-matrix / Coverage 100 Gate",
                "shared-codecov-analytics / Codecov Analytics",
                "codeql / CodeQL",
                "shared-scanner-matrix / QLTY Zero",
                "shared-scanner-matrix / Sonar Zero",
                "shared-scanner-matrix / Codacy Zero",
                "shared-scanner-matrix / Semgrep Zero",
                "shared-scanner-matrix / Sentry Zero",
                "shared-scanner-matrix / DeepScan Zero",
                "SonarCloud Code Analysis",
                "Codacy Static Code Analysis",
                "DeepScan",
                "qlty check",
                "qlty coverage",
                "qlty coverage diff",
                "Chromatic Playwright",
                "Applitools Visual",
            ],
        )
        # Whole-tree enforcement: push and pull_request must require the
        # exact same provider context set — nothing pull_request_only.
        self.assertEqual(
            set(active_required_contexts(profile, event_name="push")),
            set(pr_contexts),
        )
        self.assertIn("shared-scanner-matrix / QLTY Zero", pr_contexts)
        self.assertTrue(
            {"qlty check", "qlty coverage", "qlty coverage diff"}.issubset(
                pr_contexts
            )
        )
        self.assertTrue(
            {
                "shared-scanner-matrix / QLTY Zero",
                "qlty check",
                "qlty coverage",
                "qlty coverage diff",
            }.issubset(pbinfo["required_contexts"]["target"])
        )
        self.assertTrue(
            {"Chromatic Playwright", "Applitools Visual"}.issubset(
                profile["required_contexts"]["target"]
            )
        )
        self.assertEqual(pr_contexts, merge_group_contexts)

    def test_phase1_repo_verify_commands_follow_repo_contracts(self) -> None:
        """Phase-1 repos should keep their repo-specific verify commands and lanes."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")

        reframe = load_repo_profile(inventory, "Prekzursil/Reframe")
        tanks = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")
        env_inspector = load_repo_profile(inventory, "Prekzursil/env-inspector")
        swfoc = load_repo_profile(inventory, "Prekzursil/SWFOC-Mod-Menu")

        self.assertEqual(reframe["verify_command"], "make verify")
        self.assertEqual(tanks["verify_command"], "make verify")
        self.assertEqual(env_inspector["verify_command"], "make verify")
        self.assertEqual(
            swfoc["verify_command"],
            (
                "dotnet test tests/SwfocTrainer.Tests/"
                "SwfocTrainer.Tests.csproj -c Release "
                '--no-build --filter '
                '"FullyQualifiedName!~SwfocTrainer.Tests.Profiles.Live'
                '&FullyQualifiedName!~RuntimeAttachSmokeTests"'
            ),
        )
        self.assertEqual(reframe["codex_environment"]["mode"], "automatic")
        self.assertEqual(tanks["codex_environment"]["verify_command"], "make verify")
        self.assertEqual(
            reframe["codex_environment"]["auth_file"],
            "~/.codex/auth.json",
        )
        self.assertEqual(
            reframe["codex_environment"]["runner_labels"],
            ["self-hosted", "codex-trusted"],
        )

    def test_airline_enforces_all_provider_contexts_on_push_and_pr(self) -> None:
        """Airline must enforce every native + aggregator context on push AND PR.

        Previously the native DeepScan / Codacy / qlty cloud contexts (and
        the DeepScan Zero aggregator) were pull_request_only, so a direct
        push to main bypassed them. Under strict-zero whole-tree they are
        required on both events.
        """
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/Airline-Reservations-System")

        push_contexts = active_required_contexts(profile, event_name="push")
        pr_contexts = active_required_contexts(profile, event_name="pull_request")

        for required in (
            "DeepSource: JavaScript",
            "DeepSource: Python",
            "DeepSource: C & C++",
            "DeepSource: Shell",
            "DeepSource: Secrets",
            "shared-scanner-matrix / DeepScan Zero",
            "shared-scanner-matrix / QLTY Zero",
            "DeepScan",
            "Codacy Static Code Analysis",
            "qlty check",
            "qlty coverage",
            "qlty coverage diff",
        ):
            self.assertIn(required, push_contexts)
            self.assertIn(required, pr_contexts)
        # No pull_request_only holes: push and PR enforce the same set.
        self.assertEqual(set(push_contexts), set(pr_contexts))
        self.assertEqual(profile["required_contexts"]["pull_request_only"], [])

    def test_quality_zero_platform_keeps_codacy_native_context_pr_only(self) -> None:
        """The control-plane repo should not require native Codacy contexts itself."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/quality-zero-platform")

        push_contexts = active_required_contexts(profile, event_name="push")
        pr_contexts = active_required_contexts(profile, event_name="pull_request")

        self.assertNotIn("Codacy Static Code Analysis", push_contexts)
        self.assertNotIn("Codacy Static Code Analysis", pr_contexts)
        self.assertNotIn("DeepScan", pr_contexts)
        self.assertNotIn("qlty check", pr_contexts)
        self.assertNotIn("qlty coverage", pr_contexts)
        self.assertNotIn("qlty coverage diff", pr_contexts)

    def test_quality_zero_platform_requires_qlty_zero_context_on_push_and_ruleset(
        self,
    ) -> None:
        """QLTY Zero must remain required on push and rulesets."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/quality-zero-platform")

        push_contexts = active_required_contexts(profile, event_name="push")
        ruleset_contexts = active_required_contexts(profile, event_name="ruleset")
        merge_group_contexts = active_required_contexts(
            profile, event_name="merge_group"
        )
        payload = build_ruleset_payload(profile)
        ruleset_status_checks = [
            item["context"]
            for item in payload["rules"][1]["parameters"]["required_status_checks"]
        ]
        self.assertTrue(
            all(
                item == {"context": item["context"]}
                for item in payload["rules"][1]["parameters"]["required_status_checks"]
            )
        )

        self._assert_quality_zero_platform_contexts(
            "shared-codecov-analytics / Codecov Analytics",
            "shared-scanner-matrix / QLTY Zero",
            "shared-scanner-matrix / DeepSource Visible Zero",
            push_contexts=push_contexts,
            ruleset_contexts=ruleset_contexts,
            target_contexts=profile["required_contexts"]["target"],
            ruleset_status_checks=ruleset_status_checks,
        )
        self.assertEqual(ruleset_contexts, merge_group_contexts)
        self.assertNotIn("qlty coverage diff", ruleset_contexts)
        self.assertEqual(
            payload["rules"][0]["parameters"]["required_approving_review_count"],
            0,
        )
        self.assertFalse(
            payload["rules"][0]["parameters"][
                "required_review_thread_resolution"
            ]
        )

    def test_quality_zero_platform_requires_qlty_zero(self) -> None:
        """The control-plane target contexts should keep the governed QLTY lane."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/quality-zero-platform")

        push_contexts = active_required_contexts(profile, event_name="push")
        pr_contexts = active_required_contexts(profile, event_name="pull_request")
        target_contexts = set(profile["required_contexts"]["target"])

        self.assertIn("shared-codecov-analytics / Codecov Analytics", push_contexts)
        self.assertIn("shared-codecov-analytics / Codecov Analytics", pr_contexts)
        self.assertIn(
            "shared-codecov-analytics / Codecov Analytics", target_contexts
        )
        self.assertIn("shared-scanner-matrix / QLTY Zero", push_contexts)
        self.assertIn("shared-scanner-matrix / QLTY Zero", pr_contexts)
        self.assertIn("shared-scanner-matrix / QLTY Zero", target_contexts)
        self.assertIn(
            "shared-scanner-matrix / DeepSource Visible Zero", push_contexts
        )
        self.assertIn(
            "shared-scanner-matrix / DeepSource Visible Zero", pr_contexts
        )
        self.assertIn(
            "shared-scanner-matrix / DeepSource Visible Zero", target_contexts
        )
        self.assertNotIn("Codacy Static Code Analysis", target_contexts)
        self.assertNotIn("DeepScan", target_contexts)

    def test_airline_target_contexts_include_deepsource_contracts(self) -> None:
        """Airline should enforce the owned DeepSource contexts in target rulesets."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/Airline-Reservations-System")
        target_contexts = set(profile["required_contexts"]["target"])

        self.assertTrue(
            {
                "DeepSource: JavaScript",
                "DeepSource: Python",
                "DeepSource: C & C++",
                "DeepSource: Shell",
                "DeepSource: Secrets",
            }.issubset(target_contexts)
        )

    def test_active_required_contexts_falls_back_to_required_now_for_other_events(
        self,
    ) -> None:
        """Unknown events should keep the required-now contract."""
        profile = {
            "required_contexts": {
                "always": ["Coverage 100 Gate"],
                "pull_request_only": ["SonarCloud Code Analysis"],
                "required_now": [
                    "Coverage 100 Gate",
                    "SonarCloud Code Analysis",
                ],
                "target": ["Coverage 100 Gate"],
            }
        }

        self.assertEqual(
            active_required_contexts(profile, event_name="workflow_dispatch"),
            [
                "Coverage 100 Gate",
                "SonarCloud Code Analysis",
            ],
        )
