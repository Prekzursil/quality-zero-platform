from __future__ import absolute_import

import unittest
from pathlib import Path

from scripts.quality.control_plane import (
    build_ruleset_payload,
    load_inventory,
    load_repo_profile,
)


ROOT = Path(__file__).resolve().parents[1]


class RulesetPayloadTests(unittest.TestCase):
    """Validate emitted ruleset payloads for representative repos."""

    def test_pbinfo_fixture_ruleset_payload_matches_declared_contexts(self) -> None:
        """Check the pbinfo fixture uses the declared hard-zero contexts."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/pbinfo-get-unsolved")
        payload = build_ruleset_payload(profile)

        required = payload["rules"][1]["parameters"]["required_status_checks"]
        contexts = [entry["context"] for entry in required]

        self.assertEqual(payload["name"], "quality-zero-platform / pbinfo-get-unsolved")
        self.assertIn("Coverage 100 Gate", contexts)
        self.assertIn("DeepScan", contexts)
        self.assertEqual(
            payload["rules"][0]["parameters"]["required_approving_review_count"],
            0,
        )
        self.assertFalse(
            payload["rules"][0]["parameters"]["required_review_thread_resolution"]
        )
        self.assertEqual(profile["default_branch"], "main")

    def test_reframe_ruleset_payload_uses_target_contract(self) -> None:
        """Check Reframe emits the target contract expected by the profile."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/Reframe")
        payload = build_ruleset_payload(profile)

        required = payload["rules"][1]["parameters"]["required_status_checks"]
        contexts = [entry["context"] for entry in required]

        self.assertIn("CodeQL", contexts)
        self.assertIn("Codacy Static Code Analysis", contexts)
        self.assertIn("Chromatic Playwright", contexts)
        self.assertIn("Applitools Visual", contexts)
        self.assertIn("qlty check", contexts)
        self.assertIn("qlty coverage", contexts)
        self.assertIn("qlty coverage diff", contexts)
        self.assertIn("Chromatic Playwright", profile["required_contexts"]["target"])
        self.assertIn("qlty check", profile["required_contexts"]["target"])

    def test_env_inspector_ruleset_payload_matches_emitted_required_contexts(
        self,
    ) -> None:
        """Check env-inspector keeps the emitted contexts aligned with the profile."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/env-inspector")
        payload = build_ruleset_payload(profile)

        required = payload["rules"][1]["parameters"]["required_status_checks"]
        contexts = [entry["context"] for entry in required]

        self.assertIn("Codecov Analytics", contexts)
        self.assertIn("QLTY Zero", contexts)
        self.assertIn("qlty check", contexts)
        self.assertIn("qlty coverage", contexts)
        self.assertIn("qlty coverage diff", contexts)
        self.assertIn("SonarCloud Code Analysis", contexts)

    def test_airline_ruleset_payload_requires_qlty_contexts(self) -> None:
        """Check Airline keeps the qlty and DeepSource contexts in the ruleset."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/Airline-Reservations-System")
        payload = build_ruleset_payload(profile)

        required = payload["rules"][1]["parameters"]["required_status_checks"]
        contexts = [entry["context"] for entry in required]

        self.assertIn("QLTY Zero", contexts)
        self.assertIn("qlty check", contexts)
        self.assertIn("qlty coverage", contexts)
        self.assertIn("qlty coverage diff", contexts)
        self.assertIn("DeepSource: JavaScript", contexts)
        self.assertIn("DeepSource: Python", contexts)
        self.assertIn("DeepSource: C & C++", contexts)
        self.assertIn("DeepSource: Shell", contexts)
        self.assertIn("DeepSource: Secrets", contexts)

    def test_quality_zero_platform_self_ruleset_enforces_qlty_coverage_contexts(
        self,
    ) -> None:
        """Check the platform self-ruleset omits repo-only qlty contexts."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/quality-zero-platform")
        payload = build_ruleset_payload(profile)

        contexts = [
            entry["context"]
            for entry in payload["rules"][1]["parameters"]["required_status_checks"]
        ]

        self.assertNotIn("qlty check", contexts)
        self.assertNotIn("qlty coverage", contexts)
        self.assertNotIn("qlty coverage diff", contexts)
        self.assertNotIn("Codacy Static Code Analysis", contexts)
        self.assertNotIn("DeepScan", contexts)
        self.assertNotIn("qlty check", profile["required_contexts"]["target"])
        self.assertNotIn("qlty coverage", profile["required_contexts"]["target"])
        self.assertNotIn("qlty coverage diff", profile["required_contexts"]["target"])
        self.assertNotIn(
            "Codacy Static Code Analysis",
            profile["required_contexts"]["target"],
        )
        self.assertNotIn(
            "DeepScan",
            profile["required_contexts"]["target"],
        )
        self.assertIn("Codecov Analytics", contexts)
        self.assertIn("QLTY Zero", contexts)
