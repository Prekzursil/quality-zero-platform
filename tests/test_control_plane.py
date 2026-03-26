from __future__ import absolute_import

import io
import json
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Dict
from unittest.mock import patch

from scripts.quality.control_plane import (
    active_required_contexts,
    build_ruleset_payload,
    load_inventory,
    load_repo_profile,
    validate_profile,
)
from scripts.quality import export_profile as export_profile_module


ROOT = Path(__file__).resolve().parents[1]


class ControlPlaneTests(unittest.TestCase):
    @staticmethod
    def _special_repo_profiles() -> Dict[str, dict]:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        return {
            "devextreme": load_repo_profile(inventory, "Prekzursil/DevExtreme-Filter-Go-Language"),
            "reframe": load_repo_profile(inventory, "Prekzursil/Reframe"),
            "momentstudio": load_repo_profile(inventory, "Prekzursil/momentstudio"),
            "env_inspector": load_repo_profile(inventory, "Prekzursil/env-inspector"),
            "airline": load_repo_profile(inventory, "Prekzursil/Airline-Reservations-System"),
            "swfoc": load_repo_profile(inventory, "Prekzursil/SWFOC-Mod-Menu"),
            "quality_zero_platform": load_repo_profile(inventory, "Prekzursil/quality-zero-platform"),
        }

    def _assert_airline_existing_behaviors(self, profile: dict) -> None:
        airline_inputs = {(item["format"], item["name"], item["path"]) for item in profile["coverage"]["inputs"]}
        self.assertEqual(
            airline_inputs,
            {
                ("xml", "scripts", "coverage/python/coverage.xml"),
                ("lcov", "node", "airline-gui/coverage/lcov.info"),
                ("lcov", "cpp", "coverage/cpp/lcov.info"),
            },
        )
        self.assertIn(
            "python -m pytest -q tests/test_quality_security_scripts.py tests/test_quality_script_coverage.py",
            profile["coverage"]["command"],
        )
        self.assertEqual(profile["coverage"]["require_sources"], ["scripts/", "src/", "airline-gui/src/"])
        self.assertIn("--filter '.*/src/.*'", profile["coverage"]["command"])
        self.assertIn("--exclude '.*/build/_deps/.*'", profile["coverage"]["command"])

    def _assert_swfoc_existing_behaviors(self, profile: dict) -> None:
        self.assertEqual(profile["coverage"]["assert_mode"]["pull_request"], "non_regression")
        self.assertEqual(profile["coverage"]["runner"], "windows-latest")
        self.assertEqual(profile["visual_lane"]["kind"], "desktop-adapter")

    def test_inventory_expands_to_15_repos(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        self.assertEqual(len(inventory["repos"]), 15)

    def test_common_phase1_template_contexts_resolve(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")
        pbinfo = load_repo_profile(inventory, "Prekzursil/pbinfo-get-unsolved")
        pr_contexts = active_required_contexts(profile, event_name="pull_request")

        self.assertEqual(profile["stack"], "node-frontend")
        self.assertEqual(
            active_required_contexts(profile, event_name="push"),
            [
                "Coverage 100 Gate",
                "Codecov Analytics",
                "QLTY Zero",
                "Sonar Zero",
                "Codacy Zero",
                "Semgrep Zero",
                "Sentry Zero",
                "DeepScan Zero",
                "SonarCloud Code Analysis",
            ],
        )
        self.assertIn("QLTY Zero", pr_contexts)
        self.assertTrue({"qlty check", "qlty coverage", "qlty coverage diff"}.issubset(pr_contexts))
        self.assertTrue(
            {"QLTY Zero", "qlty check", "qlty coverage", "qlty coverage diff"}.issubset(pbinfo["required_contexts"]["target"])
        )
        self.assertTrue({"Chromatic Playwright", "Applitools Visual"}.issubset(profile["required_contexts"]["target"]))

    def test_phase1_repo_verify_commands_follow_repo_contracts(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")

        reframe = load_repo_profile(inventory, "Prekzursil/Reframe")
        tanks = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")
        env_inspector = load_repo_profile(inventory, "Prekzursil/env-inspector")
        swfoc = load_repo_profile(inventory, "Prekzursil/SWFOC-Mod-Menu")

        self.assertEqual(reframe["verify_command"], "make verify")
        self.assertEqual(tanks["verify_command"], "make verify")
        self.assertEqual(env_inspector["verify_command"], "make verify")
        self.assertEqual(
            swfoc["verify_command"],
            (
                "dotnet test tests/SwfocTrainer.Tests/SwfocTrainer.Tests.csproj -c Release "
                '--no-build --filter "FullyQualifiedName!~SwfocTrainer.Tests.Profiles.Live'
                '&FullyQualifiedName!~RuntimeAttachSmokeTests"'
            ),
        )
        self.assertEqual(reframe["codex_environment"]["mode"], "automatic")
        self.assertEqual(tanks["codex_environment"]["verify_command"], "make verify")
        self.assertEqual(reframe["codex_environment"]["auth_file"], "~/.codex/auth.json")
        self.assertEqual(reframe["codex_environment"]["runner_labels"], ["self-hosted", "codex-trusted"])

    def test_airline_keeps_deepscan_contexts_pr_only(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/Airline-Reservations-System")

        push_contexts = active_required_contexts(profile, event_name="push")
        pr_contexts = active_required_contexts(profile, event_name="pull_request")

        self.assertNotIn("DeepScan Zero", push_contexts)
        self.assertNotIn("DeepScan", push_contexts)
        self.assertNotIn("Codacy Static Code Analysis", push_contexts)
        self.assertNotIn("qlty check", push_contexts)
        self.assertNotIn("qlty coverage", push_contexts)
        self.assertNotIn("qlty coverage diff", push_contexts)
        self.assertIn("QLTY Zero", push_contexts)
        self.assertIn("DeepScan Zero", pr_contexts)
        self.assertIn("DeepScan", pr_contexts)
        self.assertIn("Codacy Static Code Analysis", pr_contexts)
        self.assertIn("QLTY Zero", pr_contexts)
        self.assertIn("qlty check", pr_contexts)
        self.assertIn("qlty coverage", pr_contexts)
        self.assertIn("qlty coverage diff", pr_contexts)

    def test_quality_zero_platform_keeps_codacy_native_context_pr_only(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/quality-zero-platform")

        push_contexts = active_required_contexts(profile, event_name="push")
        pr_contexts = active_required_contexts(profile, event_name="pull_request")

        self.assertNotIn("Codacy Static Code Analysis", push_contexts)
        self.assertNotIn("Codacy Static Code Analysis", pr_contexts)
        self.assertNotIn("DeepScan", pr_contexts)
        self.assertNotIn("qlty check", pr_contexts)
        self.assertNotIn("qlty coverage", pr_contexts)
        self.assertNotIn("qlty coverage diff", pr_contexts)

    def test_quality_zero_platform_requires_controller_qlty_zero_context_on_push_and_ruleset(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/quality-zero-platform")

        push_contexts = active_required_contexts(profile, event_name="push")
        ruleset_contexts = active_required_contexts(profile, event_name="ruleset")
        payload = build_ruleset_payload(profile)

        self.assertIn("QLTY Zero", push_contexts)
        self.assertIn("QLTY Zero", profile["required_contexts"]["target"])
        self.assertIn("QLTY Zero", ruleset_contexts)
        self.assertNotIn("qlty coverage diff", ruleset_contexts)
        self.assertIn("QLTY Zero", [item["context"] for item in payload["rules"][1]["parameters"]["required_status_checks"]])

    def test_quality_zero_platform_requires_qlty_zero(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/quality-zero-platform")

        push_contexts = active_required_contexts(profile, event_name="push")
        pr_contexts = active_required_contexts(profile, event_name="pull_request")
        target_contexts = set(active_required_contexts(profile, event_name="target"))

        self.assertIn("QLTY Zero", push_contexts)
        self.assertIn("QLTY Zero", pr_contexts)
        self.assertIn("QLTY Zero", target_contexts)
        self.assertNotIn("Codacy Static Code Analysis", target_contexts)
        self.assertNotIn("DeepScan", target_contexts)

    def test_codex_session_manager_profile_tracks_windows_wpf_rollout_contract(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/codex-session-manager")

        push_contexts = active_required_contexts(profile, event_name="push")
        pr_contexts = active_required_contexts(profile, event_name="pull_request")
        target_contexts = set(active_required_contexts(profile, event_name="target"))

        self.assertEqual(profile["stack"], "dotnet-wpf")
        self.assertEqual(profile["verify_command"], "bash scripts/verify")
        self.assertEqual(profile["coverage"]["runner"], "windows-latest")
        self.assertEqual(profile["coverage"]["shell"], "pwsh")
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
        self.assertEqual(
            profile["vendors"]["sonar"]["project_key"],
            "Prekzursil_codex-session-manager",
        )
        self.assertTrue({"build-test", "analyze", "scan", "dependency-review"}.issubset(push_contexts))
        self.assertTrue({"build-test", "analyze", "scan", "dependency-review"}.issubset(target_contexts))
        self.assertTrue(
            {
                "qlty check",
                "qlty coverage",
                "qlty coverage diff",
                "Codacy Static Code Analysis",
                "DeepScan",
            }.issubset(pr_contexts)
        )

    def test_reframe_overlay_adds_visual_and_platform_contexts_to_target(self) -> None:
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
        profiles = self._special_repo_profiles()
        reframe_inputs = {(item["format"], item["name"], item["path"]) for item in profiles["reframe"]["coverage"]["inputs"]}
        self.assertEqual(
            reframe_inputs,
            {
                ("lcov", "web", "apps/web/coverage/lcov.info"),
                ("lcov", "desktop-ts", "apps/desktop/coverage/lcov.info"),
            },
        )
        momentstudio_inputs = {
            (item["format"], item["name"], item["path"]) for item in profiles["momentstudio"]["coverage"]["inputs"]
        }
        self.assertEqual(
            momentstudio_inputs,
            {
                ("xml", "backend", "backend/coverage.xml"),
                ("lcov", "frontend", "frontend/coverage/lcov.info"),
            },
        )
        self.assertIn('grep -vE "/ent($|/)"', profiles["devextreme"]["coverage"]["command"])
        self.assertIn(
            "go test $packages -coverprofile=coverage/go-coverage.out -covermode=count",
            profiles["devextreme"]["coverage"]["command"],
        )
        self.assertEqual(profiles["env_inspector"]["coverage"]["min_percent"], 100.0)
        self.assertIn("env_inspector.py", profiles["env_inspector"]["coverage"]["require_sources"])

    def test_special_repo_coverage_profiles_capture_existing_behaviors(self) -> None:
        profiles = self._special_repo_profiles()
        self._assert_airline_existing_behaviors(profiles["airline"])
        self._assert_swfoc_existing_behaviors(profiles["swfoc"])

    def test_quality_zero_platform_profile_keeps_controller_specific_contracts(self) -> None:
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

    def test_quality_zero_platform_keeps_codecov_target_only_until_provider_check_emits(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/quality-zero-platform")

        pr_contexts = active_required_contexts(profile, event_name="pull_request")
        target_contexts = profile["required_contexts"]["target"]

        self.assertNotIn("Codecov Analytics", pr_contexts)
        self.assertIn("Codecov Analytics", target_contexts)

    def test_env_inspector_overlay_aligns_push_required_contexts_to_emitted_surface(self) -> None:
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

    def test_provider_metadata_tracks_real_qlty_names_and_visual_repo_tokens(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")

        quality_zero_platform = load_repo_profile(inventory, "Prekzursil/quality-zero-platform")
        reframe = load_repo_profile(inventory, "Prekzursil/Reframe")
        webcoder = load_repo_profile(inventory, "Prekzursil/WebCoder")
        swfoc = load_repo_profile(inventory, "Prekzursil/SWFOC-Mod-Menu")

        self.assertEqual(
            quality_zero_platform["vendors"]["qlty"]["check_names_actual"],
            ["qlty check", "qlty coverage", "qlty coverage diff"],
        )
        self.assertEqual(quality_zero_platform["vendors"]["qlty"]["gate_context"], "qlty check")
        self.assertEqual(quality_zero_platform["vendors"]["qlty"]["coverage_context"], "qlty coverage")
        self.assertEqual(quality_zero_platform["vendors"]["qlty"]["diff_coverage_context"], "qlty coverage diff")
        self.assertEqual(quality_zero_platform["vendors"]["qlty"]["diff_coverage_percent"], 100)
        self.assertEqual(quality_zero_platform["vendors"]["qlty"]["total_coverage_policy"], "fail_on_any_drop")
        self.assertEqual(quality_zero_platform["vendors"]["codacy"]["profile_mode"], "defaults_all_languages")

        self.assertEqual(reframe["vendors"]["chromatic"]["project_name"], "Reframe")
        self.assertEqual(reframe["vendors"]["chromatic"]["token_secret"], "CHROMATIC_PROJECT_TOKEN")
        self.assertEqual(reframe["vendors"]["chromatic"]["local_env_var"], "CHROMATIC_PROJECT_TOKEN_REFRAME")
        self.assertEqual(reframe["vendors"]["applitools"]["project_name"], "Reframe")
        self.assertEqual(webcoder["vendors"]["chromatic"]["local_env_var"], "CHROMATIC_PROJECT_TOKEN_WEBCODER")
        self.assertEqual(swfoc["vendors"]["chromatic"]["local_env_var"], "CHROMATIC_PROJECT_TOKEN_SWFOC_MOD_MENU")

    def test_visual_pair_validation_flags_single_context(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")
        profile["required_contexts"]["target"] = [name for name in profile["required_contexts"]["target"] if name != "Applitools Visual"]

        findings = validate_profile(profile)

        self.assertTrue(any("visual_pair_required" in item for item in findings))

    def test_visual_pair_validation_requires_provider_metadata(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")

        profile["vendors"]["chromatic"].pop("project_name", None)
        profile["vendors"]["chromatic"].pop("token_secret", None)
        profile["vendors"]["chromatic"].pop("local_env_var", None)
        profile["vendors"]["applitools"].pop("project_name", None)

        findings = validate_profile(profile)

        self.assertIn(
            "Prekzursil/TanksFlashMobile: visual_pair_required requires chromatic.project_name",
            findings,
        )
        self.assertIn(
            "Prekzursil/TanksFlashMobile: visual_pair_required requires chromatic.token_secret",
            findings,
        )
        self.assertIn(
            "Prekzursil/TanksFlashMobile: visual_pair_required requires chromatic.local_env_var",
            findings,
        )
        self.assertIn(
            "Prekzursil/TanksFlashMobile: visual_pair_required requires applitools.project_name",
            findings,
        )

    def test_validate_profile_flags_invalid_vendor_url(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")
        profile["vendors"]["codacy"]["dashboard_url"] = "invalid.example.com"

        findings = validate_profile(profile)

        self.assertTrue(any("invalid codacy.dashboard_url" in item for item in findings))

    def test_export_profile_emits_coverage_inputs_for_codecov_and_qlty_uploads(self) -> None:
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
            self.assertIn("coverage_input_files=repo/coverage/platform-coverage.xml", output)
            self.assertIn("qlty_coverage_files=repo/coverage/platform-coverage.xml", output)

    def test_export_profile_emits_all_airline_coverage_inputs_for_codecov_and_qlty_uploads(self) -> None:
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

    def test_export_profile_script_prints_json_when_output_path_is_not_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            stdout = io.StringIO()
            output_path = Path(tmpdir) / "github-output.txt"
            argv = [
                "export_profile.py",
                "--repo-slug",
                "Prekzursil/quality-zero-platform",
                "--github-output",
                str(output_path),
            ]
            repo_root = str(ROOT)
            sys_path = [entry for entry in sys.path if entry != repo_root]
            if "" not in sys_path:
                sys_path.insert(0, "")

            with patch.object(sys, "argv", argv), patch.object(sys, "path", sys_path), redirect_stdout(stdout):
                with self.assertRaises(SystemExit) as exc:
                    runpy.run_path(str(ROOT / "scripts" / "quality" / "export_profile.py"), run_name="__main__")

            self.assertEqual(exc.exception.code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["profile_id"], "quality-zero-platform")
            self.assertEqual(payload["coverage"]["inputs"][0]["path"], "coverage/platform-coverage.xml")


