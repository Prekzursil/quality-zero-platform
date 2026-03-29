from __future__ import absolute_import

import unittest
from pathlib import Path
from typing import List, Set

from scripts.quality.control_plane import active_required_contexts, load_inventory, load_repo_profile, validate_profile

from tests.control_plane_support import ControlPlaneAssertions

ROOT = Path(__file__).resolve().parents[1]


class CodexSessionManagerControlPlaneTests(ControlPlaneAssertions, unittest.TestCase):
    def _load_profile(self) -> dict:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        return load_repo_profile(inventory, "Prekzursil/codex-session-manager")

    def _assert_context_contracts(
        self,
        *,
        push_contexts: List[str],
        pr_contexts: List[str],
        ruleset_contexts: Set[str],
        target_contexts: Set[str],
    ) -> None:
        self._assert_context_subset(
            push_contexts,
            self._zero_gate_provider_contexts() | {"build-test", "analyze", "scan"},
        )
        self._assert_context_subset(
            pr_contexts,
            self._zero_gate_provider_contexts()
            | {"build-test", "analyze", "scan", "dependency-review"},
        )
        self._assert_context_subset(
            ruleset_contexts,
            self._shared_zero_gate_contexts()
            | {
                "build-test",
                "analyze",
                "scan",
                "dependency-review",
                "aggregate-gate / Quality Zero Gate",
                "shared-scanner-matrix / Quality Rollup",
            },
        )
        self.assertTrue(ruleset_contexts.issubset(target_contexts))
        for unexpected in (
            "qlty check",
            "qlty coverage",
            "qlty coverage diff",
            "Codacy Static Code Analysis",
            "DeepScan",
            "shared-codecov-analytics / Codecov Analytics",
            "shared-scanner-matrix / Coverage 100 Gate",
            "shared-scanner-matrix / QLTY Zero",
            "shared-scanner-matrix / Sonar Zero",
            "shared-scanner-matrix / Codacy Zero",
            "shared-scanner-matrix / Semgrep Zero",
            "shared-scanner-matrix / Sentry Zero",
            "shared-scanner-matrix / DeepScan Zero",
            "aggregate-gate / Quality Zero Gate",
            "shared-scanner-matrix / Quality Rollup",
        ):
            self.assertNotIn(unexpected, pr_contexts)

    def _assert_profile_shape(self, profile: dict) -> None:
        self.assertEqual(profile["stack"], "dotnet-wpf")
        self.assertEqual(profile["verify_command"], "bash scripts/verify")
        self.assertEqual(profile["coverage"]["runner"], "windows-latest")
        self.assertEqual(profile["coverage"]["shell"], "pwsh")
        self.assertNotIn("command_shell", profile["coverage"])
        self.assertEqual(
            [(item["name"], item["path"], item["format"]) for item in profile["coverage"]["inputs"]],
            [
                ("app", "coverage/app/coverage.cobertura.xml", "xml"),
                ("core", "coverage/core/coverage.cobertura.xml", "xml"),
                ("storage", "coverage/storage/coverage.cobertura.xml", "xml"),
            ],
        )
        self.assertEqual(
            profile["coverage"]["require_sources"],
            [
                "src/CodexSessionManager.App/",
                "src/CodexSessionManager.Core/",
                "src/CodexSessionManager.Storage/",
            ],
        )
        self.assertEqual(profile["vendors"]["sonar"]["project_key"], "Prekzursil_codex-session-manager")

    def test_codex_session_manager_uses_repo_specific_required_contexts(self) -> None:
        profile = self._load_profile()

        pr_contexts = active_required_contexts(profile, event_name="pull_request")
        push_contexts = active_required_contexts(profile, event_name="push")
        ruleset_contexts = set(active_required_contexts(profile, event_name="ruleset"))
        target_contexts = set(profile["required_contexts"]["target"])

        for required in self._zero_gate_provider_contexts() | {
            "build-test",
            "analyze",
            "scan",
        }:
            self.assertIn(required, push_contexts)
            self.assertIn(required, pr_contexts)
        self.assertIn("dependency-review", pr_contexts)
        self.assertNotIn("dependency-review", push_contexts)

        for required in self._shared_zero_gate_contexts() | {
            "build-test",
            "analyze",
            "scan",
            "dependency-review",
            "aggregate-gate / Quality Zero Gate",
            "shared-scanner-matrix / Quality Rollup",
        }:
            self.assertIn(required, ruleset_contexts)
            self.assertIn(required, target_contexts)

        for unexpected in (
            "qlty check",
            "qlty coverage",
            "qlty coverage diff",
            "shared-scanner-matrix / Coverage 100 Gate",
            "shared-scanner-matrix / QLTY Zero",
            "shared-scanner-matrix / Sonar Zero",
            "shared-scanner-matrix / Codacy Zero",
            "shared-scanner-matrix / Semgrep Zero",
            "shared-scanner-matrix / Sentry Zero",
            "shared-scanner-matrix / DeepScan Zero",
            "shared-codecov-analytics / Codecov Analytics",
            "aggregate-gate / Quality Zero Gate",
            "shared-scanner-matrix / Quality Rollup",
        ):
            self.assertNotIn(unexpected, push_contexts)
            self.assertNotIn(unexpected, pr_contexts)

    def test_codex_session_manager_profile_validation_accepts_emitted_required_now_contexts(self) -> None:
        findings = validate_profile(self._load_profile())
        self.assertEqual(
            [item for item in findings if "required_contexts.required_now" in item],
            [],
        )

    def test_codex_session_manager_profile_tracks_windows_wpf_rollout_contract(self) -> None:
        profile = self._load_profile()

        push_contexts = active_required_contexts(profile, event_name="push")
        pr_contexts = active_required_contexts(profile, event_name="pull_request")
        ruleset_contexts = set(active_required_contexts(profile, event_name="ruleset"))
        target_contexts = set(profile["required_contexts"]["target"])

        self._assert_profile_shape(profile)
        self._assert_context_contracts(
            push_contexts=push_contexts,
            pr_contexts=pr_contexts,
            ruleset_contexts=ruleset_contexts,
            target_contexts=target_contexts,
        )
