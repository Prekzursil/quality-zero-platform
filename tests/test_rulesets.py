from __future__ import annotations

import unittest
from pathlib import Path

from scripts.quality.control_plane import (
    build_ruleset_payload,
    load_inventory,
    load_repo_profile,
)


ROOT = Path(__file__).resolve().parents[1]


class RulesetPayloadTests(unittest.TestCase):
    def test_pbinfo_fixture_ruleset_payload_matches_declared_contexts(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/pbinfo-get-unsolved")
        payload = build_ruleset_payload(profile)

        required = payload["rules"][1]["parameters"]["required_status_checks"]
        contexts = [entry["context"] for entry in required]

        self.assertEqual(payload["name"], "quality-zero-platform / pbinfo-get-unsolved")
        self.assertIn("Coverage 100 Gate", contexts)
        self.assertIn("DeepScan", contexts)
        self.assertEqual(profile["default_branch"], "main")

    def test_reframe_ruleset_payload_uses_live_required_now_not_target(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/Reframe")
        payload = build_ruleset_payload(profile)

        required = payload["rules"][1]["parameters"]["required_status_checks"]
        contexts = [entry["context"] for entry in required]

        self.assertIn("CodeQL", contexts)
        self.assertIn("Codacy Static Code Analysis", contexts)
        self.assertNotIn("Chromatic Playwright", contexts)
        self.assertNotIn("Applitools Visual", contexts)
        self.assertNotIn("Qlty Gate", contexts)
        self.assertIn("Chromatic Playwright", profile["required_contexts"]["target"])
        self.assertIn("Qlty Gate", profile["required_contexts"]["target"])

    def test_quality_zero_platform_self_ruleset_defers_qlty_until_emitted(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/quality-zero-platform")
        payload = build_ruleset_payload(profile)

        contexts = [entry["context"] for entry in payload["rules"][1]["parameters"]["required_status_checks"]]

        self.assertNotIn("Qlty Gate", contexts)
        self.assertNotIn("Qlty Coverage", contexts)
        self.assertNotIn("Qlty Diff Coverage", contexts)
        self.assertIn("Qlty Gate", profile["required_contexts"]["target"])
        self.assertIn("Qlty Coverage", profile["required_contexts"]["target"])
        self.assertIn("Qlty Diff Coverage", profile["required_contexts"]["target"])

