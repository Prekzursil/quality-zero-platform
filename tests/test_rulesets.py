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

    def test_reframe_ruleset_payload_includes_pr_only_contexts(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/Reframe")
        payload = build_ruleset_payload(profile)

        required = payload["rules"][1]["parameters"]["required_status_checks"]
        contexts = [entry["context"] for entry in required]

        self.assertIn("CodeQL", contexts)
        self.assertIn("BrowserStack E2E", contexts)
        self.assertIn("Codacy Static Code Analysis", contexts)


if __name__ == "__main__":
    unittest.main()
