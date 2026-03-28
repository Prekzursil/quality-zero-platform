from __future__ import absolute_import

import io
import json
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.quality import export_profile as export_profile_module
from scripts.quality.control_plane import (
    active_required_contexts,
    load_inventory,
    load_repo_profile,
    validate_profile,
)
from tests.control_plane_support import ControlPlaneAssertions, ROOT


class ControlPlaneProfileTests(ControlPlaneAssertions, unittest.TestCase):
    """Regression coverage for repo overlays, provider metadata, and exports."""

    def test_reframe_overlay_adds_visual_and_platform_contexts_to_target(self) -> None:
        """Reframe should retain its visual and platform-specific target overlay."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/Reframe")

        pr_contexts = active_required_contexts(profile, event_name="pull_request")
        target_contexts = set(profile["required_contexts"]["target"])

        for name in (
            "Python API & worker checks",
            "Web build",
            "Analyze (python)",
            "Analyze (typescript)",
            "CodeQL",
            "CodeRabbit",
        ):
            self.assertIn(name, pr_contexts)
        for name in (
            "qlty check",
            "qlty coverage",
            "qlty coverage diff",
            "Chromatic Playwright",
            "Applitools Visual",
        ):
            self.assertIn(name, target_contexts)

    def test_special_repo_coverage_profiles_capture_multi_language_inputs(self) -> None:
        """Special repos should keep their expected multi-language coverage inputs."""
        profiles = self._special_repo_profiles()
        reframe_inputs = {
            (item["format"], item["name"], item["path"])
            for item in profiles["reframe"]["coverage"]["inputs"]
        }
        self.assertEqual(
            reframe_inputs,
            {
                ("lcov", "web", "apps/web/coverage/lcov.info"),
                ("lcov", "desktop-ts", "apps/desktop/coverage/lcov.info"),
            },
        )
        momentstudio_inputs = {
            (item["format"], item["name"], item["path"])
            for item in profiles["momentstudio"]["coverage"]["inputs"]
        }
        self.assertEqual(
            momentstudio_inputs,
            {
                ("xml", "backend", "backend/coverage.xml"),
                ("lcov", "frontend", "frontend/coverage/lcov.info"),
            },
        )
        self.assertIn(
            'grep -vE "/ent($|/)"',
            profiles["devextreme"]["coverage"]["command"],
        )
        self.assertIn(
            "go test $packages -coverprofile=coverage/go-coverage.out -covermode=count",
            profiles["devextreme"]["coverage"]["command"],
        )
        self.assertEqual(profiles["env_inspector"]["coverage"]["min_percent"], 100.0)
        self.assertIn(
            "env_inspector.py",
            profiles["env_inspector"]["coverage"]["require_sources"],
        )

    def test_special_repo_coverage_profiles_capture_existing_behaviors(self) -> None:
        """Special repos should preserve their bespoke coverage behavior contracts."""
        profiles = self._special_repo_profiles()
        self._assert_airline_existing_behaviors(profiles["airline"])
        self._assert_swfoc_existing_behaviors(profiles["swfoc"])

    def test_quality_zero_platform_profile_keeps_controller_specific_contracts(
        self,
    ) -> None:
        """The control-plane repo should keep its controller-only secret contracts."""
        profile = self._special_repo_profiles()["quality_zero_platform"]
        self.assertNotIn("DEEPSCAN_POLICY_MODE", profile["required_vars"])
        self.assertEqual(profile["github_mutation_lane"], "codex-private-runner")
        self.assertEqual(profile["codex_auth_lane"], "chatgpt-account")
        self.assertNotIn("OPENAI_API_KEY", profile["required_secrets"])
        self.assertIn("CODEX_AUTH_JSON", profile["conditional_secrets"])
        self.assertEqual(
            profile["issue_policy"],
            {
                "mode": "ratchet",
                "pr_behavior": "introduced_only",
                "main_behavior": "absolute",
                "baseline_ref": "main",
            },
        )

    def test_quality_zero_platform_keeps_codecov_target_only_until_provider_check_emits(
        self,
    ) -> None:
        """Codecov should stay target-only until the provider emits a native check."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/quality-zero-platform")

        pr_contexts = active_required_contexts(profile, event_name="pull_request")
        target_contexts = profile["required_contexts"]["target"]

        self.assertNotIn("Codecov Analytics", pr_contexts)
        self.assertIn("Codecov Analytics", target_contexts)

    def test_env_inspector_overlay_aligns_push_required_contexts_to_emitted_surface(
        self,
    ) -> None:
        """Env Inspector push contexts should match the emitted green surface."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/env-inspector")

        push_contexts = active_required_contexts(profile, event_name="push")
        pr_contexts = active_required_contexts(profile, event_name="pull_request")
        target_contexts = set(profile["required_contexts"]["target"])

        self.assertEqual(
            push_contexts,
            [
                "Coverage 100 Gate",
                "Codecov Analytics",
                "QLTY Zero",
                "Sonar Zero",
                "Codacy Zero",
                "Semgrep Zero",
                "Sentry Zero",
                "DeepScan Zero",
            ],
        )
        self.assertTrue(
            {
                "Coverage 100 Gate",
                "Codecov Analytics",
                "QLTY Zero",
                "Sonar Zero",
                "Codacy Zero",
                "Semgrep Zero",
                "Sentry Zero",
                "DeepScan Zero",
                "SonarCloud Code Analysis",
                "Codacy Static Code Analysis",
                "DeepScan",
                "qlty check",
                "qlty coverage",
                "qlty coverage diff",
            }.issubset(pr_contexts)
        )
        self.assertIn("Codecov Analytics", target_contexts)
        self.assertIn("QLTY Zero", target_contexts)
        self.assertIn("qlty coverage", target_contexts)
        self.assertIn("qlty coverage diff", target_contexts)
        for unexpected in ("qlty coverage", "qlty coverage diff"):
            self.assertNotIn(unexpected, push_contexts)

    def test_event_link_profile_installs_lizard_and_enforces_branch_coverage(
        self,
    ) -> None:
        """Event-link should publish a verify/coverage contract that reaches 100/100."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/event-link")

        self.assertEqual(profile["verify_command"], "bash scripts/verify")
        self.assertEqual(profile["coverage"]["min_percent"], 100.0)
        self.assertEqual(profile["coverage"]["branch_min_percent"], 100.0)
        self.assertEqual(profile["coverage"]["command"].strip(), "bash scripts/verify")
        self.assertEqual(
            profile["coverage"]["inputs"],
            [
                {"format": "xml", "name": "backend", "path": "backend/coverage.xml"},
                {
                    "format": "xml",
                    "name": "frontend",
                    "path": "ui/coverage/cobertura-coverage.xml",
                },
            ],
        )

    def test_provider_metadata_tracks_real_qlty_names(self) -> None:
        """Provider metadata should expose the expected QLTY names and policy values."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        quality_zero_platform = load_repo_profile(
            inventory, "Prekzursil/quality-zero-platform"
        )
        self.assertEqual(
            quality_zero_platform["vendors"]["qlty"]["check_names_actual"],
            ["qlty check", "qlty coverage", "qlty coverage diff"],
        )
        self.assertEqual(
            quality_zero_platform["vendors"]["qlty"]["gate_context"],
            "qlty check",
        )
        self.assertEqual(
            quality_zero_platform["vendors"]["qlty"]["coverage_context"],
            "qlty coverage",
        )
        self.assertEqual(
            quality_zero_platform["vendors"]["qlty"]["diff_coverage_context"],
            "qlty coverage diff",
        )
        self.assertEqual(
            quality_zero_platform["vendors"]["qlty"]["diff_coverage_percent"],
            100,
        )
        self.assertEqual(
            quality_zero_platform["vendors"]["qlty"]["total_coverage_policy"],
            "fail_on_any_drop",
        )
        self.assertEqual(
            quality_zero_platform["vendors"]["codacy"]["profile_mode"],
            "defaults_all_languages",
        )

    def test_provider_metadata_tracks_reframe_visual_tokens(self) -> None:
        """Reframe should keep its expected Chromatic and Applitools metadata."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        reframe = load_repo_profile(inventory, "Prekzursil/Reframe")

        self.assertEqual(reframe["vendors"]["chromatic"]["project_name"], "Reframe")
        self.assertEqual(
            reframe["vendors"]["chromatic"]["token_secret"],
            "CHROMATIC_PROJECT_TOKEN",
        )
        self.assertEqual(
            reframe["vendors"]["chromatic"]["local_env_var"],
            "CHROMATIC_PROJECT_TOKEN_REFRAME",
        )
        self.assertEqual(reframe["vendors"]["applitools"]["project_name"], "Reframe")

    def test_provider_metadata_tracks_repo_specific_visual_env_vars(self) -> None:
        """Repo-specific visual metadata should keep the expected env-var wiring."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        webcoder = load_repo_profile(inventory, "Prekzursil/WebCoder")
        swfoc = load_repo_profile(inventory, "Prekzursil/SWFOC-Mod-Menu")

        self.assertEqual(
            webcoder["vendors"]["chromatic"]["local_env_var"],
            "CHROMATIC_PROJECT_TOKEN_WEBCODER",
        )
        self.assertEqual(
            swfoc["vendors"]["chromatic"]["local_env_var"],
            "CHROMATIC_PROJECT_TOKEN_SWFOC_MOD_MENU",
        )

    def test_visual_pair_validation_flags_single_context(self) -> None:
        """Removing one visual context should trigger the visual-pair validator."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")
        profile["required_contexts"]["target"] = [
            name
            for name in profile["required_contexts"]["target"]
            if name != "Applitools Visual"
        ]

        findings = validate_profile(profile)

        self.assertTrue(any("visual_pair_required" in item for item in findings))

    def test_visual_pair_validation_requires_provider_metadata(self) -> None:
        """Visual-pair validation should require the expected provider metadata fields."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")

        profile["vendors"]["chromatic"].pop("project_name", None)
        profile["vendors"]["chromatic"].pop("token_secret", None)
        profile["vendors"]["chromatic"].pop("local_env_var", None)
        profile["vendors"]["applitools"].pop("project_name", None)

        findings = validate_profile(profile)

        self.assertIn(
            (
                "Prekzursil/TanksFlashMobile: visual_pair_required "
                "requires chromatic.project_name"
            ),
            findings,
        )
        self.assertIn(
            (
                "Prekzursil/TanksFlashMobile: visual_pair_required "
                "requires chromatic.token_secret"
            ),
            findings,
        )
        self.assertIn(
            (
                "Prekzursil/TanksFlashMobile: visual_pair_required "
                "requires chromatic.local_env_var"
            ),
            findings,
        )
        self.assertIn(
            (
                "Prekzursil/TanksFlashMobile: visual_pair_required "
                "requires applitools.project_name"
            ),
            findings,
        )

    def test_validate_profile_flags_invalid_vendor_url(self) -> None:
        """Invalid provider dashboard URLs should be reported by profile validation."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")
        profile["vendors"]["codacy"]["dashboard_url"] = "invalid.example.com"

        findings = validate_profile(profile)

        self.assertTrue(
            any("invalid codacy.dashboard_url" in item for item in findings)
        )

    def test_export_profile_emits_coverage_inputs_for_codecov_and_qlty_uploads(
        self,
    ) -> None:
        """Exported profile output should include the coverage inputs for uploads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "github-output.txt"
            with patch.object(
                sys,
                "argv",
                [
                    "export_profile.py",
                    "--repo-slug",
                    "Prekzursil/quality-zero-platform",
                    "--github-output",
                    str(output_path),
                    "--out-json",
                    str(Path(tmpdir) / "profile.json"),
                ],
            ):
                self.assertEqual(export_profile_module.main(), 0)

            output = output_path.read_text(encoding="utf-8")
            self.assertIn("codecov_enabled=true", output)
            self.assertIn(
                "coverage_input_files=repo/coverage/platform-coverage.xml",
                output,
            )
            self.assertIn(
                "qlty_coverage_files=repo/coverage/platform-coverage.xml",
                output,
            )

    def test_export_profile_emits_airline_coverage_inputs_for_codecov_and_qlty_uploads(
        self,
    ) -> None:
        """Airline exports should include all three coverage artifacts for uploads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "github-output.txt"
            with patch.object(
                sys,
                "argv",
                [
                    "export_profile.py",
                    "--repo-slug",
                    "Prekzursil/Airline-Reservations-System",
                    "--github-output",
                    str(output_path),
                    "--out-json",
                    str(Path(tmpdir) / "profile.json"),
                ],
            ):
                self.assertEqual(export_profile_module.main(), 0)

            output = output_path.read_text(encoding="utf-8")
            expected_files = (
                "coverage_input_files="
                "repo/coverage/python/coverage.xml,"
                "repo/airline-gui/coverage/lcov.info,"
                "repo/coverage/cpp/lcov.info"
            )
            self.assertIn(expected_files, output)
            self.assertIn(
                "qlty_coverage_files="
                "repo/coverage/python/coverage.xml,"
                "repo/airline-gui/coverage/lcov.info,"
                "repo/coverage/cpp/lcov.info",
                output,
            )

    def test_export_profile_script_prints_json_when_output_path_is_not_requested(
        self,
    ) -> None:
        """The export script should print JSON when no output path is requested."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "github-output.txt"
            argv = [
                "export_profile.py",
                "--repo-slug",
                "Prekzursil/quality-zero-platform",
                "--github-output",
                str(output_path),
            ]
            repo_root = str(ROOT)
            without_empty = [
                entry for entry in sys.path if entry not in (repo_root, "")
            ]

            for sys_path in (without_empty, ["", *without_empty]):
                if "" not in sys_path:
                    sys_path.insert(0, "")

                stdout = io.StringIO()
                with (
                    patch.object(sys, "argv", argv),
                    patch.object(sys, "path", sys_path),
                    redirect_stdout(stdout),
                    self.assertRaises(SystemExit) as exc,
                ):
                    runpy.run_path(
                        str(ROOT / "scripts" / "quality" / "export_profile.py"),
                        run_name="__main__",
                    )

                self.assertEqual(exc.exception.code, 0)
                payload = json.loads(stdout.getvalue())
                self.assertEqual(payload["profile_id"], "quality-zero-platform")
                self.assertEqual(
                    payload["coverage"]["inputs"][0]["path"],
                    "coverage/platform-coverage.xml",
                )

    def test_export_profile_script_handles_existing_empty_sys_path_entry(self) -> None:
        """The export script should tolerate an existing empty sys.path entry."""
        stdout = io.StringIO()
        argv = [
            "export_profile.py",
            "--repo-slug",
            "Prekzursil/quality-zero-platform",
        ]
        repo_root = str(ROOT)
        sys_path = [
            "",
            *(entry for entry in sys.path if entry not in (repo_root, "")),
        ]

        with (
            patch.object(sys, "argv", argv),
            patch.object(sys, "path", sys_path),
            redirect_stdout(stdout),
            self.assertRaises(SystemExit) as exc,
        ):
            runpy.run_path(
                str(ROOT / "scripts" / "quality" / "export_profile.py"),
                run_name="__main__",
            )

        self.assertEqual(exc.exception.code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["profile_id"], "quality-zero-platform")
