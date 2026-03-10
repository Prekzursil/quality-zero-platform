from __future__ import annotations

import unittest
from pathlib import Path

from scripts.quality.control_plane import (
    active_required_contexts,
    load_inventory,
    load_repo_profile,
    validate_profile,
)


ROOT = Path(__file__).resolve().parents[1]


class ControlPlaneTests(unittest.TestCase):
    def test_common_phase1_template_contexts_resolve(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")

        self.assertEqual(profile["stack"], "node-frontend")
        self.assertEqual(
            active_required_contexts(profile, event_name="push"),
            [
                "Coverage 100 Gate",
                "Codecov Analytics",
                "Sonar Zero",
                "Codacy Zero",
                "Semgrep Zero",
                "Sentry Zero",
                "DeepScan Zero",
                "SonarCloud Code Analysis",
                "Codacy Static Code Analysis",
                "DeepScan",
            ],
        )

    def test_phase1_repo_verify_commands_follow_repo_contracts(self) -> None:
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
            'dotnet test tests/SwfocTrainer.Tests/SwfocTrainer.Tests.csproj -c Release --no-build --filter "FullyQualifiedName!~SwfocTrainer.Tests.Profiles.Live&FullyQualifiedName!~RuntimeAttachSmokeTests"',
        )

    def test_airline_keeps_deepscan_contexts_pr_only(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/Airline-Reservations-System")

        push_contexts = active_required_contexts(profile, event_name="push")
        pr_contexts = active_required_contexts(profile, event_name="pull_request")

        self.assertNotIn("DeepScan Zero", push_contexts)
        self.assertNotIn("DeepScan", push_contexts)
        self.assertIn("DeepScan Zero", pr_contexts)
        self.assertIn("DeepScan", pr_contexts)

    def test_reframe_overlay_adds_visual_and_platform_contexts(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/Reframe")

        pr_contexts = active_required_contexts(profile, event_name="pull_request")

        for name in (
            "Python API & worker checks",
            "Web build",
            "Analyze (python)",
            "Analyze (typescript)",
            "Percy Visual",
            "Applitools Visual",
            "BrowserStack E2E",
            "CodeQL",
            "CodeRabbit",
        ):
            self.assertIn(name, pr_contexts)

    def test_special_repo_coverage_profiles_capture_existing_behaviors(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")

        reframe = load_repo_profile(inventory, "Prekzursil/Reframe")
        env_inspector = load_repo_profile(inventory, "Prekzursil/env-inspector")
        swfoc = load_repo_profile(inventory, "Prekzursil/SWFOC-Mod-Menu")

        reframe_inputs = {(item["format"], item["name"], item["path"]) for item in reframe["coverage"]["inputs"]}
        self.assertEqual(
            reframe_inputs,
            {
                ("lcov", "web", "apps/web/coverage/lcov.info"),
                ("lcov", "desktop-ts", "apps/desktop/coverage/lcov.info"),
            },
        )
        self.assertEqual(env_inspector["coverage"]["min_percent"], 100.0)
        self.assertIn("env_inspector.py", env_inspector["coverage"]["require_sources"])
        self.assertEqual(swfoc["coverage"]["assert_mode"]["pull_request"], "evidence_only")
        self.assertEqual(swfoc["coverage"]["runner"], "windows-latest")

    def test_validate_profile_flags_invalid_vendor_url(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")
        profile["vendors"]["codacy"]["dashboard_url"] = "http://insecure.example.com"

        findings = validate_profile(profile)

        self.assertTrue(any("invalid codacy.dashboard_url" in item for item in findings))


if __name__ == "__main__":
    unittest.main()
