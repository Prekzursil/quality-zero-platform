"""Test quality common -- normalization and vendor helpers."""


from __future__ import absolute_import

import unittest
from unittest.mock import patch

from scripts.quality import profile_coverage_normalization
from scripts.quality.common import (
    _deep_merge,
    finalize_vendors,
    normalize_codex_environment,
    normalize_coverage,
    normalize_coverage_assert_mode,
    normalize_deps,
    normalize_issue_policy,
    normalize_coverage_setup,
    normalize_java_setup,
)


from tests._quality_common_helpers import (
    inferred_coverage as _inferred_coverage_helper,
    normalized_explicit_coverage as _explicit_coverage_helper,
)


class QualityCommonExtraTests(unittest.TestCase):
    """QualityCommonExtraTests."""

    @staticmethod
    def _normalized_explicit_coverage() -> dict:
        return _explicit_coverage_helper()

    @staticmethod
    def _inferred_coverage() -> dict:
        return _inferred_coverage_helper()


    def test_normalize_setup_helpers_cover_string_inputs(self) -> None:
        """Cover normalize setup helpers cover string inputs."""
        self.assertEqual(
            normalize_java_setup("21"), {"distribution": "temurin", "version": "21"}
        )
        self.assertEqual(
            normalize_java_setup(None), {"distribution": "", "version": ""}
        )
        self.assertEqual(
            normalize_coverage_setup(
                {
                    "python": " 3.12 ",
                    "node": " 20 ",
                    "go": " 1.22 ",
                    "dotnet": " 8 ",
                    "rust": 1,
                    "system_packages": [" curl ", "git", "curl", "  "],
                    "java": "17",
                }
            ),
            {
                "python": "3.12",
                "node": "20",
                "go": "1.22",
                "dotnet": "8",
                "rust": True,
                "system_packages": ["curl", "git"],
                "java": {"distribution": "temurin", "version": "17"},
            },
        )
        self.assertEqual(normalize_coverage_setup(None), normalize_coverage_setup({}))
        self.assertEqual(
            normalize_coverage_assert_mode("strict"), {"default": "strict"}
        )
        self.assertEqual(normalize_coverage_assert_mode(None), {"default": "enforce"})
        self.assertEqual(
            normalize_coverage_assert_mode(
                {"default": "", "python": " warn ", "javascript": " "}
            ),
            {"default": "enforce", "python": "warn"},
        )
        self.assertEqual(
            normalize_coverage_assert_mode({"default": "non_regression"}),
            {"default": "non_regression"},
        )

    def test_normalize_coverage_helper_covers_explicit_inputs(self) -> None:
        """Cover normalize coverage helper covers explicit inputs."""
        self.assertEqual(
            self._normalized_explicit_coverage(),
            {
                "runner": "ubuntu-latest",
                "shell": "bash",
                "command": "qlty check",
                "inputs": [
                    {"format": "xml", "name": "coverage", "path": "coverage.xml"}
                ],
                "require_sources": ["source-a", "source-b"],
                "require_sources_mode": "explicit",
                "min_percent": 98.5,
                "branch_min_percent": None,
                "assert_mode": {"default": "enforce", "python": "warn"},
                "evidence_note": "note",
                "setup": {
                    "python": "3.11",
                    "node": "20",
                    "go": "1.22",
                    "dotnet": "8",
                    "rust": True,
                    "system_packages": ["git", "curl"],
                    "java": {"distribution": "temurin", "version": "21"},
                },
            },
        )

    def test_normalize_coverage_helper_infers_required_sources_from_command(
        self,
    ) -> None:
        """Cover normalize coverage helper infers required sources from command."""
        inferred = self._inferred_coverage()
        self.assertEqual(
            inferred["require_sources"],
            [
                "scripts/",
                "scripts/quality/assert_coverage_100.py",
                "scripts/quality/check_sentry_zero.py",
                "src/",
                "airline-gui/src/",
            ],
        )
        self.assertEqual(inferred["require_sources_mode"], "infer")
        self.assertIsNone(inferred["branch_min_percent"])
        self.assertIsNone(
            normalize_coverage({"branch_min_percent": "bogus"})["branch_min_percent"]
        )

    def test_profile_coverage_normalization_helpers_cover_empty_and_multi_filter_paths(
        self,
    ) -> None:
        """Cover profile coverage normalization helpers cover empty and multi filter paths."""
        with patch.object(
            profile_coverage_normalization, "_normalize_source_hint", return_value=""
        ):
            self.assertEqual(
                profile_coverage_normalization._extract_cov_hints("--cov=scripts"), []
            )

        fake_match = type(
            "FakeMatch",
            (),
            {"group": staticmethod(lambda _name: "alpha")},
        )
        with patch.object(
            profile_coverage_normalization,
            "_GCOVR_FILTER_RE",
            type(
                "FakeRegex",
                (),
                {
                    "finditer": staticmethod(
                        lambda _command: [fake_match(), fake_match()]
                    )
                },
            )(),
        ), patch.object(
            profile_coverage_normalization,
            "_normalize_source_hint",
            side_effect=["alpha/", ""],
        ):
            self.assertEqual(
                profile_coverage_normalization._extract_gcovr_hints(
                    "gcovr --filter placeholder"
                ),
                ["alpha/"],
            )

    def test_normalize_codex_environment_helper_covers_string_inputs(self) -> None:
        """Cover normalize codex environment helper covers string inputs."""
        self.assertEqual(
            normalize_codex_environment(None, verify_command="bash scripts/verify"),
            {
                "mode": "automatic",
                "verify_command": "bash scripts/verify",
                "auth_file": "~/.codex/auth.json",
                "network_profile": "unrestricted",
                "methods": "all",
                "runner_labels": ["self-hosted", "codex-trusted"],
            },
        )

    def test_normalize_issue_policy_supports_defaults_and_shortcuts(self) -> None:
        """Cover normalize issue policy supports defaults and shortcuts."""
        self.assertEqual(
            normalize_issue_policy(None),
            {
                "mode": "ratchet",
                "pr_behavior": "introduced_only",
                "main_behavior": "absolute",
                "baseline_ref": "main",
            },
        )
        self.assertEqual(
            normalize_issue_policy("zero"),
            {
                "mode": "zero",
                "pr_behavior": "absolute",
                "main_behavior": "absolute",
                "baseline_ref": "",
            },
        )
        self.assertEqual(
            normalize_issue_policy(
                {
                    "mode": "audit",
                    "pr_behavior": "introduced_only",
                    "main_behavior": "absolute",
                }
            ),
            {
                "mode": "audit",
                "pr_behavior": "introduced_only",
                "main_behavior": "absolute",
                "baseline_ref": "main",
            },
        )

    def test_normalize_deps_supports_defaults_and_shortcuts(self) -> None:
        """Cover normalize deps supports defaults and shortcuts."""
        self.assertEqual(
            normalize_deps(None),
            {
                "enabled": False,
                "policy": "zero_critical",
                "scope": "runtime",
            },
        )
        self.assertEqual(
            normalize_deps({"enabled": True, "policy": "zero_high", "scope": "all"}),
            {
                "enabled": True,
                "policy": "zero_high",
                "scope": "all",
            },
        )
        self.assertEqual(
            normalize_codex_environment(
                {
                    "mode": " manual ",
                    "verify_command": " python -m pytest ",
                    "auth_file": " ~/.codex/auth.json ",
                    "network_profile": " restricted ",
                    "methods": " changed ",
                    "runner_labels": [
                        " self-hosted ",
                        "self-hosted",
                        "codex-trusted",
                        "",
                    ],
                },
                verify_command="bash scripts/verify",
            ),
            {
                "mode": "manual",
                "verify_command": "python -m pytest",
                "auth_file": "~/.codex/auth.json",
                "network_profile": "restricted",
                "methods": "changed",
                "runner_labels": ["self-hosted", "codex-trusted"],
            },
        )

    def test_finalize_vendors_and_deep_merge_cover_string_inputs(self) -> None:
        """Cover finalize vendors and deep merge cover string inputs."""
        self.assertEqual(
            finalize_vendors(
                {
                    "vendors": {"nested": {"left": 1}, "plain": 1},
                    "providers": {"nested": {"right": 2}, "extra": 3},
                }
            ),
            {"nested": {"left": 1, "right": 2}, "plain": 1, "extra": 3},
        )
        self.assertEqual(
            _deep_merge(
                {"left": {"keep": 1}, "shared": 1},
                {"left": {"add": 2}, "shared": 3, "extra": 4},
            ),
            {"left": {"keep": 1, "add": 2}, "shared": 3, "extra": 4},
        )
