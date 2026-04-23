"""Shape-level coverage for the v2 profile schema extensions.

The v2 schema extends v1 additively (see ``docs/QZP-V2-DESIGN.md`` §3):

* ``version: 2`` marks a profile as opting into the v2 contract.
* ``mode`` (with nested ``phase``, ``shadow_until``, ``ratchet``) replaces
  ``issue_policy`` for governance-phase declaration.
* ``scanners`` replaces ``enabled_scanners`` (keyed by scanner name,
  values carry only a ``severity`` — ``block | warn | info``).
* ``overrides`` is a structured list of deliberate template deviations
  with ``reason`` + ``expires``.

These tests lock in that ``validate_profile_shape`` accepts those keys
without raising unexpected-key findings, while continuing to reject
typos or truly unknown keys. Downstream normalisation wiring arrives in
later commits — the shape accepts the fields first so v1 + v2 profiles
can coexist during the migration window.
"""

from __future__ import absolute_import

import unittest

from scripts.quality.profile_shape import validate_profile_shape


class ProfileSchemaV2ShapeTests(unittest.TestCase):
    """Accept v2 schema keys; reject unknown ones."""

    def test_v1_profile_shape_remains_valid(self) -> None:
        """An existing v1-style profile must not start reporting findings."""
        v1_profile = {
            "slug": "Prekzursil/example",
            "stack": "fullstack-web",
            "enabled_scanners": {"deepsource_visible": True},
            "issue_policy": {"mode": "ratchet", "pr_behavior": "introduced_only"},
            "coverage": {"min_percent": 100.0, "inputs": []},
        }
        self.assertEqual(
            validate_profile_shape(v1_profile, slug="Prekzursil/example"),
            [],
        )

    def test_v2_top_level_keys_accepted(self) -> None:
        """A profile declaring version/mode/scanners/overrides must not warn."""
        v2_profile = {
            "slug": "Prekzursil/example",
            "stack": "fullstack-web",
            "version": 2,
            "mode": {
                "phase": "absolute",
                "shadow_until": None,
                "ratchet": {
                    "baseline": {"coverage_overall": 100.0},
                    "target_date": "2026-09-30",
                    "escalation_date": "2026-12-31",
                },
            },
            "scanners": {
                "codeql": {"severity": "block"},
                "sonarcloud": {"severity": "block"},
                "socket_project_report": {"severity": "info"},
            },
            "overrides": [
                {
                    "file": "ui/vite.config.ts",
                    "key": "coverage.thresholds.functions",
                    "value": 90,
                    "reason": "legacy wrappers (#42)",
                    "expires": "2026-12-31",
                }
            ],
            "coverage": {
                "min_percent": 100.0,
                "inputs": [
                    {
                        "name": "backend",
                        "flag": "backend",
                        "path": "backend/coverage.xml",
                        "format": "xml",
                        "min_percent": 100.0,
                    }
                ],
            },
        }
        self.assertEqual(
            validate_profile_shape(v2_profile, slug="Prekzursil/example"),
            [],
        )

    def test_mode_nested_keys_accepted(self) -> None:
        """All documented keys inside ``mode`` must be recognised."""
        profile = {
            "slug": "Prekzursil/example",
            "mode": {
                "phase": "ratchet",
                "shadow_until": "2026-05-01",
                "ratchet": {"target_date": "2026-06-30"},
            },
        }
        self.assertEqual(
            validate_profile_shape(profile, slug="Prekzursil/example"),
            [],
        )

    def test_mode_rejects_typo_in_nested_key(self) -> None:
        """A typo inside ``mode`` must still surface as a finding."""
        profile = {
            "slug": "Prekzursil/example",
            "mode": {"phase": "absolute", "ratchett": {}},  # typo
        }
        findings = validate_profile_shape(profile, slug="Prekzursil/example")
        self.assertEqual(len(findings), 1)
        self.assertIn("unexpected mode key `ratchett`", findings[0])

    def test_unknown_top_level_key_still_rejected(self) -> None:
        """v2 is additive; typos or unknown keys must not silently pass."""
        profile = {
            "slug": "Prekzursil/example",
            "versions": 2,  # typo of `version`
        }
        findings = validate_profile_shape(profile, slug="Prekzursil/example")
        self.assertEqual(len(findings), 1)
        self.assertIn("unexpected profile key `versions`", findings[0])


if __name__ == "__main__":
    unittest.main()
