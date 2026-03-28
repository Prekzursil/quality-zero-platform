from __future__ import absolute_import

import unittest
from pathlib import Path
from typing import Dict

from scripts.quality.control_plane import load_inventory, load_repo_profile


ROOT = Path(__file__).resolve().parents[1]


class ControlPlaneAssertions(unittest.TestCase):
    """Shared helpers for control-plane regression tests."""

    @staticmethod
    def _special_repo_profiles() -> Dict[str, dict]:
        """Load repo profiles that have custom multi-language or platform overlays."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        return {
            "devextreme": load_repo_profile(
                inventory, "Prekzursil/DevExtreme-Filter-Go-Language"
            ),
            "reframe": load_repo_profile(inventory, "Prekzursil/Reframe"),
            "momentstudio": load_repo_profile(inventory, "Prekzursil/momentstudio"),
            "env_inspector": load_repo_profile(inventory, "Prekzursil/env-inspector"),
            "airline": load_repo_profile(
                inventory, "Prekzursil/Airline-Reservations-System"
            ),
            "swfoc": load_repo_profile(inventory, "Prekzursil/SWFOC-Mod-Menu"),
            "quality_zero_platform": load_repo_profile(
                inventory, "Prekzursil/quality-zero-platform"
            ),
        }

    def _assert_airline_existing_behaviors(self, profile: dict) -> None:
        """Pin the Airline profile's multi-language coverage contract and thresholds."""
        airline_inputs = {
            (item["format"], item["name"], item["path"])
            for item in profile["coverage"]["inputs"]
        }
        self.assertEqual(
            airline_inputs,
            {
                ("xml", "scripts", "coverage/python/coverage.xml"),
                ("lcov", "node", "airline-gui/coverage/lcov.info"),
                ("lcov", "cpp", "coverage/cpp/lcov.info"),
            },
        )
        for expected_snippet in (
            "tests/test_quality_security_scripts.py",
            "tests/test_quality_coverage_scripts.py",
            "tests/test_quality_script_main_flows.py",
            "tests/test_security_helpers.py",
            "tests/test_static_remediation_guards.py",
        ):
            self.assertIn(expected_snippet, profile["coverage"]["command"])
        self.assertEqual(
            profile["coverage"]["require_sources"],
            ["scripts/", "src/", "airline-gui/src/"],
        )
        self.assertIn("--filter '.*/src/.*'", profile["coverage"]["command"])
        self.assertIn("--exclude '.*/build/_deps/.*'", profile["coverage"]["command"])
        self.assertNotIn("normalize_lcov", profile["coverage"]["command"])
        self.assertEqual(profile["coverage"]["min_percent"], 100.0)
        self.assertEqual(profile["coverage"]["branch_min_percent"], 100.0)

    def _assert_swfoc_existing_behaviors(self, profile: dict) -> None:
        """Keep the SWFOC profile on its existing visual and non-regression contract."""
        self.assertEqual(
            profile["coverage"]["assert_mode"]["pull_request"],
            "non_regression",
        )
        self.assertEqual(profile["coverage"]["runner"], "windows-latest")
        self.assertEqual(profile["visual_lane"]["kind"], "desktop-adapter")
