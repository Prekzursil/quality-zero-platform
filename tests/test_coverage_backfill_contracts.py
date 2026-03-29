"""Additional coverage backfill contract tests."""

from __future__ import absolute_import

import unittest
from typing import List
from unittest.mock import Mock, patch

from scripts import security_helpers
from scripts.quality import (
    profile_contract_validation,
    profile_coverage_normalization,
    profile_normalization,
    profile_shape,
)
from tests.test_coverage_backfill import build_valid_contract_profile


def _invalid_contract_profile() -> dict:
    """Return a minimally invalid contract profile for validation tests."""
    return {
        "slug": "owner/repo",
        "required_secrets": [],
        "conditional_secrets": [],
        "issue_policy": {
            "mode": "broken",
            "pr_behavior": "broken",
            "main_behavior": "broken",
        },
        "deps": {"policy": "broken", "scope": "broken"},
        "enabled_scanners": {"coverage": True},
        "coverage": {
            "command": "cmd",
            "inputs": [],
            "shell": "cmd",
            "assert_mode": {"default": "broken"},
            "require_sources_mode": "broken",
        },
        "vendors": {
            "chromatic": {"status_context": "Chromatic"},
            "applitools": {"status_context": "Applitools"},
        },
        "visual_pair_required": True,
        "required_contexts": {
            "target": ["Chromatic"],
            "required_now": ["Chromatic"],
            "always": [],
            "pull_request_only": [],
        },
    }


def _invalid_contract_profile_findings() -> List[str]:
    """Return the validation findings for the baseline invalid profile."""
    return profile_contract_validation.validate_profile(
        _invalid_contract_profile(),
        active_required_contexts_fn=lambda _profile, event_name: ["Chromatic"],
    )


class CoverageBackfillContractTests(unittest.TestCase):
    """Coverage backfill contract tests."""

    def test_profile_validation_requires_one_ruleset_context(self) -> None:
        """Cover the empty-ruleset required-context validation branch."""
        findings = profile_contract_validation.validate_profile(
            build_valid_contract_profile(),
            active_required_contexts_fn=lambda _profile, event_name: [],
        )
        self.assertTrue(
            any("at least one required context is required" in item for item in findings)
        )

    def test_profile_validation_flags_missing_emitted_ruleset_contexts(self) -> None:
        """Cover the missing emitted ruleset target validation branch."""
        profile = build_valid_contract_profile()
        profile["required_contexts"]["target"] = ["Coverage 100 Gate", "QLTY Zero"]
        findings = profile_contract_validation.validate_profile(
            profile,
            active_required_contexts_fn=lambda _profile, event_name: ["Coverage 100 Gate"],
        )
        self.assertTrue(
            any(
                "emitted ruleset contexts are missing QLTY Zero" in item
                for item in findings
            )
        )

    def test_invalid_contract_profile_findings_cover_validation_rules(self) -> None:
        """Cover the invalid contract profile validation branches."""
        findings = _invalid_contract_profile_findings()
        expected_fragments = (
            "issue_policy.mode must be zero, ratchet, or audit",
            "deps.policy must be zero_critical, zero_high, or zero_any",
            "coverage.require_sources_mode must be explicit, infer, or disabled",
            "visual_pair_required",
            "issue_policy.main_behavior must be absolute",
        )
        for fragment in expected_fragments:
            self.assertTrue(any(fragment in item for item in findings), fragment)

    def test_profile_shape_ignores_non_mapping_coverage_payload(self) -> None:
        """Cover profile shape ignores non mapping coverage payload."""
        findings = profile_shape.validate_profile_shape(
            {"slug": "owner/repo", "coverage": "not-a-dict"}, slug="owner/repo"
        )
        self.assertEqual(findings, [])

    def test_profile_validation_requires_ratchet_baseline_ref(self) -> None:
        """Cover profile validation requires ratchet baseline ref."""
        valid_profile = build_valid_contract_profile()
        ratchet_findings = profile_contract_validation.validate_profile(
            valid_profile,
            active_required_contexts_fn=lambda _profile, event_name: [
                "Coverage 100 Gate"
            ],
        )
        self.assertTrue(
            any(
                "issue_policy.baseline_ref is required when mode is ratchet" in item
                for item in ratchet_findings
            )
        )

    def test_profile_normalization_helpers_cover_edge_branches(self) -> None:
        """Cover profile normalization helpers cover edge branches."""
        self.assertEqual(
            profile_coverage_normalization._normalize_source_hint("pkg.module"),
            "pkg/module.py",
        )
        self.assertEqual(profile_coverage_normalization._normalize_source_hint(""), "")
        fake_match = Mock()
        fake_match.group.return_value = "pkg"
        fake_regex = Mock()
        fake_regex.finditer.return_value = [fake_match]
        with patch.object(
            profile_coverage_normalization, "_GCOVR_FILTER_RE", fake_regex
        ):
            self.assertEqual(
                profile_coverage_normalization._extract_gcovr_hints(
                    "gcovr --filter '.*/pkg/.*'"
                ),
                ["pkg/"],
            )
        self.assertIn(
            "src/",
            profile_coverage_normalization._extract_gcovr_hints(
                "gcovr --filter '.*/src/.*'"
            ),
        )
        self.assertIn(
            "src/",
            profile_coverage_normalization._extract_gcovr_hints(
                'gcovr --filter ".*/src/.*"'
            ),
        )
        self.assertEqual(
            profile_normalization.infer_required_sources({"command": ""}), []
        )

    def test_security_helper_remaining_error_branches(self) -> None:
        """Cover security helper remaining error branches."""
        parsed = security_helpers.urlparse("https://api.github.com/repos/owner/repo")
        with self.assertRaisesRegex(TypeError, "expects keyword arguments only"):
            security_helpers._read_bytes_response(parsed, "unexpected")
        with self.assertRaisesRegex(
            TypeError, "Unexpected _read_bytes_response parameters: extra"
        ):
            security_helpers._read_bytes_response(
                parsed, headers={}, method="GET", data=None, timeout=15, extra=True
            )
        with self.assertRaisesRegex(TypeError, "expects keyword arguments only"):
            security_helpers.load_bytes_https(
                "https://api.github.com/repos/owner/repo", "unexpected"
            )
