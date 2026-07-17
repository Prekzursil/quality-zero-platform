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

from tests.control_plane_support import ROOT

from scripts.quality.control_plane import load_inventory, load_repo_profile
from scripts.quality.profile_normalization import (
    normalize_mode,
    normalize_overrides,
    normalize_profile_version,
    normalize_scanners,
)
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


class NormalizeProfileVersionTests(unittest.TestCase):
    """Schema version parser — missing / invalid input falls back to v1."""

    def test_missing_version_returns_one(self) -> None:
        """No ``version`` field ⇒ treat as v1."""
        self.assertEqual(normalize_profile_version(None), 1)

    def test_integer_two_returns_two(self) -> None:
        """Integer 2 is accepted as v2."""
        self.assertEqual(normalize_profile_version(2), 2)

    def test_string_two_returns_two(self) -> None:
        """String ``"2"`` coerces to v2."""
        self.assertEqual(normalize_profile_version("2"), 2)

    def test_unknown_value_falls_back_to_one(self) -> None:
        """Unknown values (v3+, garbage strings) fall back to v1."""
        self.assertEqual(normalize_profile_version(3), 1)
        self.assertEqual(normalize_profile_version("ratchet"), 1)


class NormalizeModeTests(unittest.TestCase):
    """``mode`` normalisation across v1 and v2 inputs."""

    def test_v2_explicit_phase_wins_over_legacy(self) -> None:
        """Explicit v2 ``mode.phase`` overrides the legacy issue_policy."""
        result = normalize_mode(
            {"phase": "shadow"},
            legacy_issue_policy={"mode": "absolute"},
        )
        self.assertEqual(result["phase"], "shadow")

    def test_legacy_ratchet_translates_to_phase_ratchet(self) -> None:
        """``issue_policy.mode = ratchet`` → ``mode.phase = ratchet``."""
        result = normalize_mode(None, legacy_issue_policy={"mode": "ratchet"})
        self.assertEqual(result["phase"], "ratchet")

    def test_legacy_zero_translates_to_phase_absolute(self) -> None:
        """``issue_policy.mode = zero`` → ``mode.phase = absolute``."""
        result = normalize_mode(None, legacy_issue_policy={"mode": "zero"})
        self.assertEqual(result["phase"], "absolute")

    def test_unknown_phase_coerces_to_absolute(self) -> None:
        """Garbage ``mode.phase`` values coerce to the strict default."""
        result = normalize_mode({"phase": "bogus"})
        self.assertEqual(result["phase"], "absolute")

    def test_shadow_until_string_preserved(self) -> None:
        """``mode.shadow_until`` passes through unchanged when a string."""
        result = normalize_mode({"phase": "shadow", "shadow_until": "2026-05-01"})
        self.assertEqual(result["shadow_until"], "2026-05-01")

    def test_ratchet_payload_normalised(self) -> None:
        """Full ratchet payload is preserved and ``on_escalation`` defaults."""
        result = normalize_mode(
            {
                "phase": "ratchet",
                "ratchet": {
                    "baseline": {"coverage_overall": 72.4},
                    "target_date": "2026-06-30",
                    "escalation_date": "2026-09-30",
                },
            }
        )
        self.assertEqual(result["ratchet"]["baseline"]["coverage_overall"], 72.4)
        self.assertEqual(result["ratchet"]["target_date"], "2026-06-30")
        self.assertEqual(result["ratchet"]["on_escalation"], "absolute")


class NormalizeScannersTests(unittest.TestCase):
    """``scanners`` normalisation — legacy fill-in and severity coercion."""

    def test_legacy_enabled_scanner_maps_to_block(self) -> None:
        """Legacy ``enabled_scanners[name]=true`` becomes severity:block."""
        result = normalize_scanners(
            None,
            legacy_enabled_scanners={"deepsource_visible": True, "codecov": False},
        )
        self.assertEqual(result, {"deepsource_visible": {"severity": "block"}})

    def test_v2_explicit_scanners_win_and_severity_coerced(self) -> None:
        """Explicit v2 scanner entries are case-normalised and coerced."""
        result = normalize_scanners(
            {
                "codeql": {"severity": "block"},
                "socket_project_report": {"severity": "INFO"},
                "weird_scanner": {"severity": "bogus"},
            },
        )
        self.assertEqual(result["codeql"]["severity"], "block")
        self.assertEqual(result["socket_project_report"]["severity"], "info")
        # Unknown severities fall back to the strict default.
        self.assertEqual(result["weird_scanner"]["severity"], "block")

    def test_v2_entries_override_legacy(self) -> None:
        """v2 ``scanners`` entries override legacy enabled_scanners severity."""
        result = normalize_scanners(
            {"deepsource_visible": {"severity": "warn"}},
            legacy_enabled_scanners={"deepsource_visible": True, "codeql": True},
        )
        self.assertEqual(result["deepsource_visible"]["severity"], "warn")
        self.assertEqual(result["codeql"]["severity"], "block")


class NormalizeOverridesTests(unittest.TestCase):
    """Override list normalisation — drops malformed entries silently."""

    def test_valid_override_accepted(self) -> None:
        """A complete override (file+key+reason) passes through untouched."""
        result = normalize_overrides(
            [
                {
                    "file": "ui/vite.config.ts",
                    "key": "coverage.thresholds.functions",
                    "value": 90,
                    "reason": "legacy wrappers (#42)",
                    "expires": "2026-12-31",
                }
            ]
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["value"], 90)

    def test_missing_reason_dropped(self) -> None:
        """An override without ``reason`` is silently dropped."""
        result = normalize_overrides(
            [{"file": "a", "key": "b", "value": 1}]  # no reason
        )
        self.assertEqual(result, [])

    def test_non_list_input_returns_empty(self) -> None:
        """Non-list inputs (string / None) yield an empty override list."""
        self.assertEqual(normalize_overrides("not a list"), [])
        self.assertEqual(normalize_overrides(None), [])


class FinalizedProfileV2FieldsTests(unittest.TestCase):
    """Loaded profiles carry the canonical v2 fields regardless of source shape.

    After Phase 1's on-disk migration every profile has ``version: 2``
    explicitly. The load-time path still has to work for any future v1
    input (e.g. a freshly bootstrapped repo that hasn't yet run the
    migration), so the loader synthesises the v2 block on both v1 and v2
    inputs. This test checks the v2 path through a real repo profile.
    """

    def test_event_link_profile_exposes_v2_derived_fields(self) -> None:
        """event-link is v2 on disk; loaded profile keeps the v2 fields intact."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/event-link")

        # Migration places version:2 explicitly; load_repo_profile must preserve it.
        self.assertEqual(profile["version"], 2)

        # mode.phase should come through as the ratchet value declared on-disk.
        self.assertIn(profile["mode"]["phase"], {"shadow", "ratchet", "absolute"})
        self.assertIn("ratchet", profile["mode"])

        # scanners dict present with valid severities (legacy ``enabled_scanners``
        # still fills in any scanner the v2 block omits).
        self.assertIsInstance(profile["scanners"], dict)
        for scanner_name, config in profile["scanners"].items():
            self.assertIn(config["severity"], {"block", "warn", "info"})
            self.assertIsInstance(scanner_name, str)

        # overrides: [] is written by the migration script.
        self.assertEqual(profile["overrides"], [])


if __name__ == "__main__":
    unittest.main()
