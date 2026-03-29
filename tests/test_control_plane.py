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

    def test_inventory_expands_to_15_repos(self) -> None:
        """Inventory should continue to expose the full enrolled repo set."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        self.assertEqual(len(inventory["repos"]), 15)

    def test_common_phase1_template_contexts_resolve(self) -> None:
        """Phase-1 overlays should keep their shared required-context defaults."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")
        pbinfo = load_repo_profile(inventory, "Prekzursil/pbinfo-get-unsolved")
        pr_contexts = active_required_contexts(profile, event_name="pull_request")

        self.assertEqual(profile["stack"], "node-frontend")
        self.assertEqual(profile["coverage"]["branch_min_percent"], 100.0)
        self.assertEqual(pbinfo["coverage"]["branch_min_percent"], 100.0)
        self.assertEqual(
            active_required_contexts(profile, event_name="push"),
            [
                "Coverage 100 Gate",
                "Codecov Analytics",
                "QLTY Zero",
                "Sonar Zero",
                "Codacy Zero",
                "Semgrep Zero",
                "Sentry Zero",
                "DeepScan Zero",
                "SonarCloud Code Analysis",
                "Chromatic Playwright",
                "Applitools Visual",
            ],
        )
        self.assertIn("QLTY Zero", pr_contexts)
        self.assertTrue(
            {"qlty check", "qlty coverage", "qlty coverage diff"}.issubset(
                pr_contexts
            )
        )
        self.assertTrue(
            {
                "QLTY Zero",
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

    def test_airline_keeps_deepscan_contexts_pr_only(self) -> None:
        """Airline should keep native DeepScan and related cloud contexts PR-only."""
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
        ):
            self.assertIn(required, push_contexts)
            self.assertIn(required, pr_contexts)
        self.assertNotIn("DeepScan Zero", push_contexts)
        self.assertNotIn("DeepScan", push_contexts)
        self.assertNotIn("Codacy Static Code Analysis", push_contexts)
        self.assertNotIn("qlty check", push_contexts)
        self.assertNotIn("qlty coverage", push_contexts)
        self.assertNotIn("qlty coverage diff", push_contexts)
        self.assertIn("QLTY Zero", push_contexts)
        self.assertIn("DeepScan Zero", pr_contexts)
        self.assertIn("DeepScan", pr_contexts)
        self.assertIn("Codacy Static Code Analysis", pr_contexts)
        self.assertIn("QLTY Zero", pr_contexts)
        self.assertIn("qlty check", pr_contexts)
        self.assertIn("qlty coverage", pr_contexts)
        self.assertIn("qlty coverage diff", pr_contexts)

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
        payload = build_ruleset_payload(profile)

        self.assertIn("QLTY Zero", push_contexts)
        self.assertIn("DeepSource Visible Zero", push_contexts)
        self.assertIn("QLTY Zero", profile["required_contexts"]["target"])
        self.assertIn("DeepSource Visible Zero", profile["required_contexts"]["target"])
        self.assertIn("QLTY Zero", ruleset_contexts)
        self.assertIn("DeepSource Visible Zero", ruleset_contexts)
        self.assertNotIn("qlty coverage diff", ruleset_contexts)
        self.assertIn(
            "QLTY Zero",
            [
                item["context"]
                for item in payload["rules"][1]["parameters"][
                    "required_status_checks"
                ]
            ],
        )
        self.assertIn(
            "DeepSource Visible Zero",
            [
                item["context"]
                for item in payload["rules"][1]["parameters"][
                    "required_status_checks"
                ]
            ],
        )
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

        self.assertIn("QLTY Zero", push_contexts)
        self.assertIn("QLTY Zero", pr_contexts)
        self.assertIn("QLTY Zero", target_contexts)
        self.assertIn("DeepSource Visible Zero", push_contexts)
        self.assertIn("DeepSource Visible Zero", pr_contexts)
        self.assertIn("DeepSource Visible Zero", target_contexts)
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
