from __future__ import annotations

import contextlib
import io
import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.quality.control_plane import (
    _infer_coverage_inputs,
    _load_stack,
    _normalize_coverage_assert_mode,
    _normalize_coverage_inputs,
    _normalize_java_setup,
    load_inventory,
    load_repo_profile,
    main,
    validate_profile,
)


ROOT = Path(__file__).resolve().parents[1]
CONTROL_PLANE_PATH = ROOT / "scripts" / "quality" / "control_plane.py"


class ControlPlaneExtraTests(unittest.TestCase):
    def test_normalize_coverage_helpers_filter_invalid_entries_and_support_legacy_path(self) -> None:
        normalized = _normalize_coverage_inputs(
            [
                {"format": "xml", "name": "platform", "path": "coverage.xml"},
                {"format": "bogus", "name": "ignored", "path": "ignored.txt"},
                "skip-me",
            ]
        )
        self.assertEqual(normalized, [{"format": "xml", "name": "platform", "path": "coverage.xml"}])
        self.assertEqual(_infer_coverage_inputs({"artifact_path": "coverage.xml"}), [{"format": "xml", "name": "default", "path": "coverage.xml"}])
        self.assertEqual(_infer_coverage_inputs({"artifact_path": "coverage.lcov"}), [{"format": "lcov", "name": "default", "path": "coverage.lcov"}])

    def test_normalize_java_setup_and_assert_mode_support_shortcuts(self) -> None:
        self.assertEqual(
            _normalize_java_setup("21"),
            {"distribution": "temurin", "version": "21"},
        )
        self.assertEqual(
            _normalize_coverage_assert_mode("evidence_only"),
            {"default": "evidence_only"},
        )

    def test_load_stack_and_profile_resolution_raise_on_invalid_inventory_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory_path = root / "inventory" / "repos.yml"
            (root / "inventory").mkdir(parents=True, exist_ok=True)
            (root / "profiles" / "stacks").mkdir(parents=True, exist_ok=True)
            (root / "profiles" / "repos").mkdir(parents=True, exist_ok=True)
            inventory_path.write_text(
                "repos:\n"
                "  - slug: Example/Repo\n"
                ,
                encoding="utf-8",
            )
            (root / "profiles" / "stacks" / "cycle-a.yml").write_text("extends: cycle-b\n", encoding="utf-8")
            (root / "profiles" / "stacks" / "cycle-b.yml").write_text("extends: cycle-a\n", encoding="utf-8")
            (root / "profiles" / "repos" / "example.yml").write_text("verify_command: bash scripts/verify\n", encoding="utf-8")

            inventory = load_inventory(inventory_path)

            with self.assertRaisesRegex(ValueError, "stack id is required"):
                _load_stack(inventory, "")
            with self.assertRaisesRegex(ValueError, "Stack inheritance cycle"):
                _load_stack(inventory, "cycle-a")
            with self.assertRaisesRegex(ValueError, "missing a profile id"):
                load_repo_profile(inventory, "Example/Repo")

    def test_validate_profile_collects_contract_and_url_findings(self) -> None:
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")

        profile["verify_command"] = ""
        profile["github_mutation_lane"] = "copilot"
        profile["codex_auth_lane"] = "local-web-only"
        profile["provider_ui_mode"] = "manual"
        profile["codex_environment"].update(
            {
                "mode": "manual",
                "verify_command": "",
                "auth_file": "",
                "network_profile": "restricted",
                "methods": "shell-only",
                "runner_labels": [],
            }
        )
        profile["required_contexts"]["required_now"] = []
        profile["required_contexts"]["target"] = ["Coverage 100 Gate"]
        profile["required_secrets"].append("OPENAI_API_KEY")
        profile["conditional_secrets"].append("OPENAI_API_KEY")
        profile["coverage"].update(
            {
                "command": "",
                "inputs": [],
                "shell": "cmd",
                "assert_mode": {"default": "invalid"},
            }
        )
        profile["vendors"]["codacy"]["dashboard_url"] = "http://insecure.example.com"

        findings = validate_profile(profile)

        expected_fragments = [
            "verify_command is required",
            "github_mutation_lane must be codex-private-runner",
            "codex_auth_lane must be chatgpt-account",
            "provider_ui_mode must be playwright-manual-login",
            "codex_environment.mode must be automatic",
            "codex_environment.verify_command is required",
            "codex_environment.auth_file is required",
            "codex_environment.network_profile must be unrestricted",
            "codex_environment.methods must be all",
            "codex_environment.runner_labels is required",
            "at least one required context is required",
            "required_contexts.required_now is missing",
            "OPENAI_API_KEY must not be part of required_secrets",
            "conditional_secrets duplicates required_secrets",
            "coverage.command is required",
            "coverage.inputs must declare at least one report",
            "coverage.shell must be bash or pwsh",
            "coverage.assert_mode.default must be enforce or evidence_only",
            "invalid codacy.dashboard_url",
        ]
        for fragment in expected_fragments:
            self.assertTrue(any(fragment in item for item in findings), fragment)

    def test_main_print_modes_emit_expected_json(self) -> None:
        outputs: list[object] = []
        for mode in ("profile", "ruleset", "contexts"):
            buffer = io.StringIO()
            with patch.object(
                sys,
                "argv",
                [
                    "control_plane.py",
                    "--inventory",
                    str(ROOT / "inventory" / "repos.yml"),
                    "--repo-slug",
                    "Prekzursil/quality-zero-platform",
                    "--print",
                    mode,
                ],
            ), contextlib.redirect_stdout(buffer):
                self.assertEqual(main(), 0)
            outputs.append(json.loads(buffer.getvalue()))

        profile_payload, ruleset_payload, contexts_payload = outputs
        self.assertEqual(profile_payload["slug"], "Prekzursil/quality-zero-platform")
        self.assertEqual(ruleset_payload["repo_slug"], "Prekzursil/quality-zero-platform")
        self.assertIn("Coverage 100 Gate", contexts_payload)

    def test_script_entrypoint_inserts_repo_root_when_missing(self) -> None:
        root_text = str(ROOT)
        original_sys_path = list(sys.path)
        trimmed_sys_path = [item for item in original_sys_path if item != root_text]
        buffer = io.StringIO()
        with patch.object(
            sys,
            "argv",
            [
                str(CONTROL_PLANE_PATH),
                "--inventory",
                str(ROOT / "inventory" / "repos.yml"),
                "--repo-slug",
                "Prekzursil/quality-zero-platform",
                "--print",
                "contexts",
            ],
        ), patch.object(sys, "path", trimmed_sys_path[:]), contextlib.redirect_stdout(buffer):
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(str(CONTROL_PLANE_PATH), run_name="__main__")

        self.assertEqual(result.exception.code, 0)
        self.assertIn("Coverage 100 Gate", json.loads(buffer.getvalue()))
