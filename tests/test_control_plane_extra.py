from __future__ import absolute_import

import contextlib
import io
import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast, Dict, List
from unittest.mock import patch

from scripts.quality import profile_shape
from scripts.quality.control_plane import (
    InventoryOverrides,
    _apply_inventory_overrides,
    _load_yaml,
    _infer_coverage_inputs,
    _load_stack,
    _merge_required_contexts,
    _normalize_codex_environment,
    _normalize_coverage,
    _normalize_coverage_assert_mode,
    _normalize_coverage_setup,
    _normalize_coverage_inputs,
    _normalize_issue_policy,
    _normalize_java_setup,
    _normalize_required_contexts,
    _validate_coverage_contract,
    _validate_vendor_urls,
    load_inventory,
    load_repo_profile,
    main,
    repo_root,
    validate_profile,
)


ROOT = Path(__file__).resolve().parents[1]
CONTROL_PLANE_PATH = ROOT / "scripts" / "quality" / "control_plane.py"


class ControlPlaneExtraTests(unittest.TestCase):
    """Exercise the extra control-plane contract helpers."""

    _INVALID_PROFILE_FINDINGS_BLOCK = """\
verify_command is required
github_mutation_lane must be codex-private-runner
codex_auth_lane must be chatgpt-account
provider_ui_mode must be playwright-manual-login
codex_environment.mode must be automatic
codex_environment.verify_command is required
codex_environment.auth_file is required
codex_environment.network_profile must be unrestricted
codex_environment.methods must be all
codex_environment.runner_labels is required
required_contexts.required_now is missing
OPENAI_API_KEY must not be part of required_secrets
conditional_secrets duplicates required_secrets
coverage.command is required
coverage.inputs must declare at least one report
coverage.shell must be bash or pwsh
coverage.assert_mode.default must be enforce, evidence_only, or non_regression
invalid codacy.dashboard_url
"""

    @staticmethod
    def _build_invalid_profile() -> Dict[str, Any]:
        """Build a deliberately invalid profile for validation tests."""
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
        profile["vendors"]["codacy"]["dashboard_url"] = "https://localhost/codacy"
        return cast(Dict[str, Any], profile)

    @staticmethod
    def _invalid_profile_findings() -> List[str]:
        """Return the expected invalid-profile findings."""
        return ControlPlaneExtraTests._INVALID_PROFILE_FINDINGS_BLOCK.strip().splitlines()

    @staticmethod
    def _expected_issue_policy() -> Dict[str, str]:
        """Return the expected ratchet-era issue policy shape."""
        return {
            "mode": "ratchet",
            "pr_behavior": "introduced_only",
            "main_behavior": "absolute",
            "baseline_ref": "main",
        }

    @staticmethod
    def _expected_python_only_setup() -> Dict[str, Any]:
        """Return the expected coverage setup after Python-only normalization."""
        return {
            "python": "3.12",
            "node": "",
            "go": "",
            "dotnet": "",
            "rust": False,
            "system_packages": [],
            "java": {"distribution": "", "version": ""},
        }

    def test_normalize_coverage_helpers_filter_invalid_entries_and_support_legacy_path(self) -> None:
        """Check the coverage helper wrappers handle invalid and legacy inputs."""
        self.assertEqual(repo_root(), ROOT)
        self.assertEqual(_normalize_coverage_inputs("not-a-list"), [])
        normalized = _normalize_coverage_inputs(
            [
                {"format": "xml", "name": "platform", "path": "coverage.xml"},
                {"format": "bogus", "name": "ignored", "path": "ignored.txt"},
                "skip-me",
            ]
        )
        self.assertEqual(normalized, [{"format": "xml", "name": "platform", "path": "coverage.xml"}])
        self.assertEqual(
            _infer_coverage_inputs({"artifact_path": "coverage.xml"}),
            [{"format": "xml", "name": "default", "path": "coverage.xml"}],
        )
        self.assertEqual(
            _infer_coverage_inputs({"artifact_path": "coverage.lcov"}),
            [{"format": "lcov", "name": "default", "path": "coverage.lcov"}],
        )

    def test_normalize_java_setup_and_assert_mode_support_shortcuts(self) -> None:
        """Check the Java and assert-mode shortcuts normalize correctly."""
        self.assertEqual(
            _normalize_java_setup("21"),
            {"distribution": "temurin", "version": "21"},
        )
        self.assertEqual(
            _normalize_coverage_assert_mode("evidence_only"),
            {"default": "evidence_only"},
        )

    def test_required_context_normalization_wrappers_cover_helper_paths(self) -> None:
        """Check required-context wrappers cover the merge and replace paths."""
        self.assertEqual(
            _normalize_required_contexts({"always": ["Coverage 100 Gate"], "pull_request_only": ["QLTY Zero"]}),
            {
                "always": ["Coverage 100 Gate"],
                "pull_request_only": ["QLTY Zero"],
                "required_now": ["Coverage 100 Gate", "QLTY Zero"],
                "target": ["Coverage 100 Gate", "QLTY Zero"],
            },
        )
        self.assertEqual(
            _merge_required_contexts({"always": ["Coverage 100 Gate"]}, {"pull_request_only": ["QLTY Zero"]}),
            {
                "always": ["Coverage 100 Gate"],
                "pull_request_only": ["QLTY Zero"],
                "required_now": ["Coverage 100 Gate", "QLTY Zero"],
                "target": ["Coverage 100 Gate", "QLTY Zero"],
            },
        )

    def test_coverage_helper_wrappers_cover_helper_paths(self) -> None:
        """Check the coverage and Codex helper wrappers cover helper paths."""
        self.assertEqual(
            _normalize_coverage_setup({"python": " 3.12 "}),
            self._expected_python_only_setup(),
        )
        self.assertEqual(
            _normalize_coverage({"runner": "", "shell": "", "setup": {"python": "3.12"}})["setup"]["python"],
            "3.12",
        )
        self.assertEqual(
            _normalize_codex_environment({}, verify_command="bash scripts/verify")["verify_command"],
            "bash scripts/verify",
        )
        self.assertEqual(
            _normalize_issue_policy({"mode": "ratchet", "baseline_ref": "main"}),
            self._expected_issue_policy(),
        )

    def test_inventory_override_wrappers_cover_helper_paths(self) -> None:
        """Check inventory overrides still layer cleanly onto a merged profile."""
        merged = _apply_inventory_overrides(
            {"verify_command": "bash scripts/verify"},
            {
                "repo_entry": {"default_branch": "main", "rollout": "phase1", "notes": "inventory note"},
                "repo_slug": "Prekzursil/quality-zero-platform",
                "profile_id": "quality-zero-platform",
                "stack_id": "python-web",
                "required_contexts_mode": "replace",
            },
        )
        expected = InventoryOverrides(
            repo_entry={"default_branch": "main", "rollout": "phase1", "notes": "inventory note"},
            repo_slug="Prekzursil/quality-zero-platform",
            profile_id="quality-zero-platform",
            stack_id="python-web",
            required_contexts_mode="replace",
        )
        self.assertEqual(merged["slug"], expected.repo_slug)
        self.assertEqual(merged["profile_id"], expected.profile_id)
        self.assertEqual(merged["stack"], expected.stack_id)
        self.assertEqual(merged["required_contexts_mode"], expected.required_contexts_mode)

    def test_load_stack_and_profile_resolution_raise_on_invalid_inventory_entries(self) -> None:
        """Check stack and profile resolution reject malformed inventory entries."""
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

    def test_yaml_and_inventory_helpers_reject_non_mapping_payloads(self) -> None:
        """Check YAML and inventory loaders reject non-mapping payloads."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mapping_path = root / "mapping.yml"
            mapping_path.write_text("- not-a-mapping\n", encoding="utf-8")
            inventory_path = root / "inventory.yml"
            inventory_path.write_text("repos: invalid\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Expected mapping"):
                _load_yaml(mapping_path)
            with self.assertRaisesRegex(ValueError, "inventory repos must be a list"):
                load_inventory(inventory_path)

    def test_validate_profile_collects_contract_and_url_findings(self) -> None:
        """Check validation reports every expected contract finding."""
        profile = self._build_invalid_profile()
        findings = validate_profile(profile)
        for fragment in self._invalid_profile_findings():
            self.assertTrue(any(fragment in item for item in findings), fragment)

    def test_validate_profile_shape_rejects_unknown_keys(self) -> None:
        """Check profile-shape validation rejects unknown keys."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/quality-zero-platform")
        profile["unexpected"] = True
        profile["coverage"]["mystery"] = 1

        findings = validate_profile(profile)

        self.assertTrue(any("unexpected profile key `unexpected`" in item for item in findings))
        self.assertTrue(any("unexpected coverage key `mystery`" in item for item in findings))

    def test_validate_profile_shape_allows_command_shell_before_normalization(self) -> None:
        """Check profile-shape validation still allows the legacy command shell key."""
        findings = profile_shape.validate_profile_shape(
            {
                "slug": "Prekzursil/codex-session-manager",
                "coverage": {
                    "command_shell": "pwsh",
                },
            },
            slug="Prekzursil/codex-session-manager",
        )

        self.assertFalse(any("unexpected coverage key `command_shell`" in item for item in findings))

    def test_additional_contract_validators_cover_repo_lookup_target_and_runner_label_edges(self) -> None:
        """Check the repo lookup, target, and runner-label edge cases."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        with self.assertRaisesRegex(KeyError, "Repo Missing/Repo not found"):
            load_repo_profile(inventory, "Missing/Repo")

        profile = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")
        profile["codex_environment"]["runner_labels"] = ["codex-trusted"]
        profile["required_contexts"]["required_now"] = ["Coverage 100 Gate"]
        profile["required_contexts"]["target"] = []

        findings = validate_profile(profile)

        self.assertTrue(any("runner_labels must include self-hosted" in item for item in findings))
        self.assertTrue(any("required_contexts.target is missing Coverage 100 Gate" in item for item in findings))

    def test_visual_pair_and_optional_validators_cover_remaining_branches(self) -> None:
        """Check the remaining visual-pair and optional-validator branches."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")

        plain_profile = load_repo_profile(inventory, "Prekzursil/quality-zero-platform")
        plain_profile["enabled_scanners"]["coverage"] = False
        plain_profile["coverage"]["command"] = ""
        plain_profile["coverage"]["inputs"] = []
        plain_profile["vendors"]["custom"] = "skip-me"

        self.assertEqual(_validate_coverage_contract(plain_profile), [])
        self.assertEqual(_validate_vendor_urls(plain_profile), [])

        visual_profile = load_repo_profile(inventory, "Prekzursil/TanksFlashMobile")
        visual_profile["required_contexts"]["target"] = [
            item
            for item in visual_profile["required_contexts"]["target"]
            if item != "Applitools Visual"
        ]

        findings = validate_profile(visual_profile)

        self.assertTrue(
            any(
                "visual_pair_required needs both Chromatic and Applitools contexts in ruleset" in item
                for item in findings
            )
        )
        non_visual = load_repo_profile(inventory, "Prekzursil/quality-zero-platform")
        self.assertEqual([item for item in validate_profile(non_visual) if "visual_pair_required" in item], [])

    def test_main_print_modes_emit_expected_json(self) -> None:
        """Check the CLI emits JSON for profile, ruleset, and contexts modes."""
        outputs: List[Any] = []
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

        profile_payload = cast(Dict[str, Any], outputs[0])
        ruleset_payload = cast(Dict[str, Any], outputs[1])
        contexts_payload = cast(List[str], outputs[2])
        self.assertEqual(profile_payload["slug"], "Prekzursil/quality-zero-platform")
        self.assertEqual(ruleset_payload["repo_slug"], "Prekzursil/quality-zero-platform")
        self.assertIn("Coverage 100 Gate", contexts_payload)

    def test_script_entrypoint_inserts_repo_root_when_missing(self) -> None:
        """Check the script entrypoint restores the repository root on sys.path."""
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
