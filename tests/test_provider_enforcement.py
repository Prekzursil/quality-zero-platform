"""Test provider enforcement helpers used by the quality-zero gate."""

from __future__ import absolute_import

import unittest

from scripts.quality.provider_enforcement import (
    PROVIDER_ZERO_CONTEXTS,
    expected_provider_contexts,
    unenforced_providers,
)


class ExpectedProviderContextsTests(unittest.TestCase):
    """Cover the enabled+blocking -> required-context mapping."""

    def test_enabled_blocking_provider_is_expected(self) -> None:
        """An enabled, block-severity provider must surface its Zero context."""
        profile = {
            "enabled_scanners": {"codacy": True},
            "scanners": {"codacy": {"severity": "block"}},
        }
        self.assertEqual(
            expected_provider_contexts(profile),
            {"codacy": "shared-scanner-matrix / Codacy Zero"},
        )

    def test_disabled_provider_is_not_expected(self) -> None:
        """A provider that is not enabled is never required."""
        profile = {
            "enabled_scanners": {"codacy": False},
            "scanners": {"codacy": {"severity": "block"}},
        }
        self.assertEqual(expected_provider_contexts(profile), {})

    def test_info_severity_provider_is_not_expected(self) -> None:
        """A wired but info-severity provider must not be demanded."""
        profile = {
            "enabled_scanners": {"sonar": True},
            "scanners": {"sonar": {"severity": "info"}},
        }
        self.assertEqual(expected_provider_contexts(profile), {})

    def test_missing_scanner_entry_defaults_to_blocking(self) -> None:
        """An enabled provider with no scanners entry is treated as blocking."""
        profile = {"enabled_scanners": {"semgrep": True}}
        self.assertEqual(
            expected_provider_contexts(profile),
            {"semgrep": "shared-scanner-matrix / Semgrep Zero"},
        )

    def test_non_mapping_scanners_block_defaults(self) -> None:
        """A malformed scanners value falls back to block semantics."""
        profile = {
            "enabled_scanners": {"qlty": True},
            "scanners": "not-a-mapping",
        }
        self.assertEqual(
            expected_provider_contexts(profile),
            {"qlty": "shared-scanner-matrix / QLTY Zero"},
        )

    def test_non_mapping_scanner_entry_blocks(self) -> None:
        """A non-mapping per-scanner entry is treated as blocking."""
        profile = {
            "enabled_scanners": {"sentry": True},
            "scanners": {"sentry": "block"},
        }
        self.assertEqual(
            expected_provider_contexts(profile),
            {"sentry": "shared-scanner-matrix / Sentry Zero"},
        )

    def test_informational_provider_never_required(self) -> None:
        """Providers on the informational allowlist are never demanded."""
        profile = {
            "enabled_scanners": {"socket_project_report": True},
            "scanners": {"socket_project_report": {"severity": "block"}},
        }
        self.assertEqual(expected_provider_contexts(profile), {})

    def test_codeql_enablement_reads_dedicated_block(self) -> None:
        """CodeQL enablement comes from the codeql.enabled block, not scanners."""
        enabled = {"codeql": {"enabled": True}}
        disabled = {"codeql": {"enabled": False}}
        self.assertEqual(
            expected_provider_contexts(enabled),
            {"codeql": "codeql / CodeQL"},
        )
        self.assertEqual(expected_provider_contexts(disabled), {})

    def test_codeql_block_must_be_mapping(self) -> None:
        """A non-mapping codeql block is treated as not-enabled."""
        self.assertEqual(expected_provider_contexts({"codeql": "yes"}), {})

    def test_non_mapping_enabled_scanners_is_safe(self) -> None:
        """A malformed enabled_scanners block yields no expected contexts."""
        self.assertEqual(expected_provider_contexts({"enabled_scanners": []}), {})


class UnenforcedProvidersTests(unittest.TestCase):
    """Cover the fail-closed detection of unenforced wired providers."""

    def test_wired_provider_absent_from_contexts_is_flagged(self) -> None:
        """A wired provider missing from the active set is a silent-pass hole."""
        profile = {
            "enabled_scanners": {"codacy": True, "sonar": True},
            "scanners": {
                "codacy": {"severity": "block"},
                "sonar": {"severity": "block"},
            },
        }
        findings = unenforced_providers(
            profile, ["shared-scanner-matrix / Sonar Zero"]
        )
        self.assertEqual(
            findings,
            [
                "codacy: enabled+blocking but required context "
                "'shared-scanner-matrix / Codacy Zero' is not enforced"
            ],
        )

    def test_all_providers_enforced_returns_empty(self) -> None:
        """When every wired provider is enforced there are no findings."""
        profile = {
            "enabled_scanners": {"codacy": True, "semgrep": True},
            "scanners": {
                "codacy": {"severity": "block"},
                "semgrep": {"severity": "block"},
            },
        }
        self.assertEqual(
            unenforced_providers(
                profile,
                [
                    "shared-scanner-matrix / Codacy Zero",
                    "shared-scanner-matrix / Semgrep Zero",
                ],
            ),
            [],
        )

    def test_findings_are_sorted_for_determinism(self) -> None:
        """Multiple holes are reported in a stable, sorted order."""
        profile = {
            "enabled_scanners": {"semgrep": True, "codacy": True},
            "scanners": {
                "semgrep": {"severity": "block"},
                "codacy": {"severity": "block"},
            },
        }
        findings = unenforced_providers(profile, [])
        self.assertEqual([f.split(":", 1)[0] for f in findings], ["codacy", "semgrep"])

    def test_context_whitespace_is_normalised(self) -> None:
        """Active contexts are matched after stripping surrounding whitespace."""
        profile = {
            "enabled_scanners": {"codacy": True},
            "scanners": {"codacy": {"severity": "block"}},
        }
        self.assertEqual(
            unenforced_providers(
                profile, ["  shared-scanner-matrix / Codacy Zero  "]
            ),
            [],
        )

    def test_mapping_covers_every_zero_context_provider(self) -> None:
        """Every mapped provider has a non-empty canonical context string."""
        self.assertTrue(PROVIDER_ZERO_CONTEXTS)
        for provider, context in PROVIDER_ZERO_CONTEXTS.items():
            self.assertTrue(provider)
            self.assertTrue(context)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
