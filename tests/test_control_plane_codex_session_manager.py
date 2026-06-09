from __future__ import absolute_import

import unittest
from pathlib import Path
from typing import List, Set

from tests.control_plane_support import ControlPlaneAssertions

from scripts.quality.control_plane import active_required_contexts, load_inventory, load_repo_profile, validate_profile

ROOT = Path(__file__).resolve().parents[1]


class CodexSessionManagerControlPlaneTests(unittest.TestCase, ControlPlaneAssertions):
    """Validate the codex-session-manager rollout contract."""

    @staticmethod
    def _load_profile() -> dict:
        """Load the governed profile used by this test class."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        return load_repo_profile(inventory, "Prekzursil/codex-session-manager")

    @staticmethod
    def _repo_required_contexts() -> Set[str]:
        """Return repo-specific contexts shared by push, PR, and ruleset checks."""
        return {"build-test", "scan"}

    @staticmethod
    def _pr_only_contexts() -> Set[str]:
        """Return contexts that only appear on pull requests and rulesets."""
        return {
            "dependency-review",
            "aggregate-gate / Quality Zero Gate",
            "shared-scanner-matrix / Quality Rollup",
        }

    @staticmethod
    def _unexpected_contexts() -> Set[str]:
        """Return contexts that must stay out of this repo contract."""
        return {
            "Codecov Analytics",
            "Coverage 100 Gate",
            "QLTY Zero",
            "Sonar Zero",
            "Codacy Zero",
            "Semgrep Zero",
            "Sentry Zero",
            "DeepScan Zero",
            "qlty check",
            "qlty coverage",
            "qlty coverage diff",
            "Codacy Static Code Analysis",
            "DeepScan",
        }

    def _assert_context_contracts(
        self,
        *,
        push_contexts: List[str],
        pr_contexts: List[str],
        ruleset_contexts: Set[str],
        target_contexts: Set[str],
    ) -> None:
        """Assert the event-specific context contract for the repo."""
        repo_required = self._repo_required_contexts()
        pr_required = (
            self._shared_zero_gate_contexts()
            | {"codeql / CodeQL"}
            | repo_required
            | self._pr_only_contexts()
        )
        self._assert_context_subset(
            push_contexts,
            self._zero_gate_provider_contexts() | {"codeql / CodeQL"} | repo_required,
        )
        self._assert_context_subset(pr_contexts, pr_required)
        self._assert_context_subset(ruleset_contexts, pr_required)
        self.assertTrue(ruleset_contexts.issubset(target_contexts))
        for unexpected in self._unexpected_contexts():
            self.assertNotIn(unexpected, pr_contexts)

    def _assert_profile_shape(self, profile: dict) -> None:
        """Assert the repo profile still matches the WPF rollout shape."""
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
        """Check the repo emits the expected PR, push, and ruleset contexts."""
        profile = self._load_profile()

        pr_contexts = active_required_contexts(profile, event_name="pull_request")
        push_contexts = active_required_contexts(profile, event_name="push")
        ruleset_contexts = set(active_required_contexts(profile, event_name="ruleset"))
        target_contexts = set(profile["required_contexts"]["target"])
        self._assert_context_contracts(
            push_contexts=push_contexts,
            pr_contexts=pr_contexts,
            ruleset_contexts=ruleset_contexts,
            target_contexts=target_contexts,
        )
        self.assertNotIn("dependency-review", push_contexts)
        for unexpected in {"qlty check", "qlty coverage", "qlty coverage diff"}:
            self.assertNotIn(unexpected, push_contexts)
            self.assertNotIn(unexpected, pr_contexts)
        for unexpected in self._unexpected_contexts() - {
            "qlty check",
            "qlty coverage",
            "qlty coverage diff",
            "Codacy Static Code Analysis",
            "DeepScan",
        }:
            self.assertNotIn(unexpected, pr_contexts)

    def test_codex_session_manager_profile_validation_accepts_emitted_required_now_contexts(self) -> None:
        """Check validation accepts the emitted required-now contexts."""
        findings = validate_profile(self._load_profile())
        self.assertEqual(
            [item for item in findings if "required_contexts.required_now" in item],
            [],
        )

    def test_codex_session_manager_profile_tracks_windows_wpf_rollout_contract(self) -> None:
        """Check the repo profile still tracks the Windows WPF rollout contract."""
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
