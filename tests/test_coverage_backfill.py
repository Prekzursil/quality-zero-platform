from __future__ import absolute_import

import importlib
import json
import os
import runpy
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.quality import (
    build_admin_dashboard,
    build_quality_rollup,
    check_dependabot_alerts,
    control_plane_admin,
    post_pr_quality_comment,
    profile_contract_validation,
    profile_coverage_normalization,
    profile_normalization,
    profile_shape,
    run_coverage_gate,
)
from scripts import security_helpers


class CoverageBackfillTests(unittest.TestCase):
    def test_dashboard_parse_args_fallback_write_and_module_entrypoint(self) -> None:
        with patch.object(sys, "argv", ["build_admin_dashboard.py", "--output-dir", "site"]):
            args = build_admin_dashboard.parse_args()
        self.assertEqual(args.output_dir, "site")

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "site"
            build_admin_dashboard.write_dashboard(output_dir, {"generated_at": "now", "repo_count": 0, "repos": []})
            self.assertTrue((output_dir / "index.html").is_file())

            script_path = Path(build_admin_dashboard.__file__).resolve()
            root_text = str(script_path.parents[2])
            trimmed_sys_path = [item for item in sys.path if item != root_text]
            module = importlib.import_module("scripts.quality.build_admin_dashboard")
            original_sys_path = list(sys.path)
            try:
                sys.path[:] = trimmed_sys_path[:]
                reloaded = importlib.reload(module)
                self.assertIn(root_text, sys.path)
                with patch.object(reloaded, "parse_args", return_value=Namespace(inventory="", output_dir=str(output_dir), assets_dir="")), patch.object(
                    reloaded, "load_inventory", return_value={"repos": []}
                ), patch.object(reloaded, "write_dashboard", return_value=None):
                    self.assertEqual(reloaded.main(), 0)
            finally:
                sys.path[:] = original_sys_path

            with patch.object(sys, "argv", [str(script_path), "--output-dir", str(output_dir)]), patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(script_path), run_name="__main__")
            self.assertEqual(result.exception.code, 0)

    def test_quality_rollup_parse_args_and_module_entrypoint(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "build_quality_rollup.py",
                "--profile-json",
                "profile.json",
                "--repo",
                "owner/repo",
                "--sha",
                "abc",
                "--artifacts-dir",
                "artifacts",
            ],
        ):
            args = build_quality_rollup.parse_args()
        self.assertEqual(args.repo, "owner/repo")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps({"slug": "owner/repo", "active_required_contexts": []}), encoding="utf-8")
            script_path = Path(build_quality_rollup.__file__).resolve()
            root_text = str(script_path.parents[2])
            original_sys_path = list(sys.path)
            try:
                sys.path[:] = [item for item in sys.path if item != root_text]
                reloaded = importlib.reload(build_quality_rollup)
                self.assertIn(root_text, sys.path)
                with patch.object(reloaded, "parse_args", return_value=Namespace(
                    profile_json=str(profile_path),
                    repo="owner/repo",
                    sha="abc",
                    artifacts_dir=str(root),
                    out_json="quality-rollup/summary.json",
                    out_md="quality-rollup/summary.md",
                )), patch.object(reloaded, "write_report", return_value=0), patch.dict("os.environ", {}, clear=True):
                    self.assertEqual(reloaded.main(), 0)
            finally:
                sys.path[:] = original_sys_path

            with patch.object(
                sys,
                "argv",
                [
                    str(script_path),
                    "--profile-json",
                    str(profile_path),
                    "--repo",
                    "owner/repo",
                    "--sha",
                    "abc",
                    "--artifacts-dir",
                    str(root),
                ],
            ), patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(script_path), run_name="__main__")
            self.assertEqual(result.exception.code, 0)

    def test_dependabot_parse_render_invalid_payload_and_module_entrypoint(self) -> None:
        with patch.object(sys, "argv", ["check_dependabot_alerts.py", "--repo", "owner/repo"]):
            args = check_dependabot_alerts._parse_args()
        self.assertEqual(args.policy, "zero_critical")
        markdown = check_dependabot_alerts._render_md(
            {"status": "pass", "repo": "owner/repo", "open_alerts": 0, "policy": "zero_critical", "scope": "runtime", "timestamp_utc": "now", "findings": []}
        )
        self.assertIn("- None", markdown)
        with patch.object(check_dependabot_alerts, "load_json_https", return_value=({"bad": True}, {})):
            with self.assertRaisesRegex(RuntimeError, "Unexpected Dependabot alerts payload"):
                check_dependabot_alerts._request_alerts("owner/repo", "token", scope="runtime")

        script_path = Path(check_dependabot_alerts.__file__).resolve()
        root_text = str(script_path.parents[2])
        trimmed_sys_path = [item for item in sys.path if item != root_text]
        with patch.object(sys, "argv", [str(script_path), "--repo", "owner/repo"]), patch.object(sys, "path", trimmed_sys_path[:]), patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(str(script_path), run_name="__main__")
        self.assertEqual(result.exception.code, 1)

    def test_control_plane_admin_load_yaml_error_and_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invalid = root / "invalid.yml"
            invalid.write_text("- not-a-mapping\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Expected mapping"):
                control_plane_admin._load_yaml(invalid)

            inventory = root / "inventory"
            profiles = root / "profiles" / "repos"
            inventory.mkdir(parents=True)
            profiles.mkdir(parents=True)
            (inventory / "repos.yml").write_text("version: 1\nrepos: []\n", encoding="utf-8")
            script_path = Path(control_plane_admin.__file__).resolve()
            root_text = str(script_path.parents[2])
            original_sys_path = list(sys.path)
            try:
                sys.path[:] = [item for item in sys.path if item != root_text]
                reloaded = importlib.reload(control_plane_admin)
                self.assertIn(root_text, sys.path)
                with patch.object(
                    sys,
                    "argv",
                    [
                        str(script_path),
                        "--repo-root",
                        str(root),
                        "enroll-repo",
                        "--repo-slug",
                        "owner/repo",
                        "--profile-id",
                        "example",
                        "--stack",
                        "python-web",
                    ],
                ):
                    with self.assertRaises(SystemExit) as result:
                        runpy.run_path(str(script_path), run_name="__main__")
                self.assertEqual(result.exception.code, 0)
            finally:
                sys.path[:] = original_sys_path

            with patch.object(control_plane_admin, "parse_args", return_value=Namespace(repo_root=str(root), command="set-scanner", profile_id="example", scanner="sonar", enabled="true")), patch.object(
                control_plane_admin, "set_scanner"
            ) as set_scanner_mock:
                self.assertEqual(control_plane_admin.main(), 0)
            set_scanner_mock.assert_called_once()

            with patch.object(control_plane_admin, "parse_args", return_value=Namespace(repo_root=str(root), command="set-issue-policy", profile_id="example", mode="ratchet", baseline_ref="main")), patch.object(
                control_plane_admin, "set_issue_policy"
            ) as set_issue_policy_mock:
                self.assertEqual(control_plane_admin.main(), 0)
            set_issue_policy_mock.assert_called_once()

    def test_post_pr_comment_request_and_module_entrypoint(self) -> None:
        with patch.object(post_pr_quality_comment, "load_json_https", return_value=({"ok": True}, {})) as load_json_mock:
            payload = post_pr_quality_comment._github_request("https://api.github.com/repos/owner/repo/issues/1/comments", "token", method="POST", data={"body": "ok"})
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(load_json_mock.call_args.kwargs["method"], "POST")

        script_path = Path(post_pr_quality_comment.__file__).resolve()
        root_text = str(script_path.parents[2])
        trimmed_sys_path = [item for item in sys.path if item != root_text]
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown = Path(temp_dir) / "rollup.md"
            markdown.write_text("# Rollup\n", encoding="utf-8")
            with patch.object(sys, "argv", [str(script_path), "--repo", "owner/repo", "--pull-request", "1", "--markdown-file", str(markdown)]), patch.object(
                sys, "path", trimmed_sys_path[:]
            ), patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(script_path), run_name="__main__")
            self.assertEqual(str(result.exception), "GITHUB_TOKEN or GH_TOKEN is required")

    def test_profile_validation_branches_and_shape_non_dict(self) -> None:
        profile = {
            "slug": "owner/repo",
            "required_secrets": [],
            "conditional_secrets": [],
            "issue_policy": {"mode": "broken", "pr_behavior": "broken", "main_behavior": "broken"},
            "deps": {"policy": "broken", "scope": "broken"},
            "enabled_scanners": {"coverage": True},
            "coverage": {"command": "cmd", "inputs": [], "shell": "cmd", "assert_mode": {"default": "broken"}, "require_sources_mode": "broken"},
            "vendors": {"chromatic": {"status_context": "Chromatic"}, "applitools": {"status_context": "Applitools"}},
            "visual_pair_required": True,
            "required_contexts": {"target": ["Chromatic"], "required_now": ["Chromatic"], "always": [], "pull_request_only": []},
        }
        findings = profile_contract_validation.validate_profile(profile, active_required_contexts_fn=lambda _profile, event_name: ["Chromatic"])
        self.assertTrue(any("issue_policy.mode must be zero, ratchet, or audit" in item for item in findings))
        self.assertTrue(any("deps.policy must be zero_critical, zero_high, or zero_any" in item for item in findings))
        self.assertTrue(any("coverage.require_sources_mode must be explicit, infer, or disabled" in item for item in findings))
        self.assertTrue(any("visual_pair_required" in item for item in findings))
        self.assertTrue(any("issue_policy.main_behavior must be absolute" in item for item in findings))

        findings = profile_shape.validate_profile_shape({"slug": "owner/repo", "coverage": "not-a-dict"}, slug="owner/repo")
        self.assertEqual(findings, [])

        valid_profile = {
            "slug": "owner/repo",
            "required_secrets": [],
            "conditional_secrets": [],
            "issue_policy": {"mode": "ratchet", "pr_behavior": "introduced_only", "main_behavior": "absolute", "baseline_ref": ""},
            "deps": {"policy": "zero_critical", "scope": "runtime"},
            "enabled_scanners": {"coverage": True},
            "coverage": {"command": "echo ok", "inputs": [{"name": "platform"}], "shell": "bash", "assert_mode": {"default": "enforce"}, "require_sources_mode": "infer"},
            "vendors": {"chromatic": {"status_context": "Chromatic", "project_name": "proj", "token_secret": "token", "local_env_var": "env"}, "applitools": {"status_context": "Applitools", "project_name": "proj"}},
            "visual_pair_required": False,
            "required_contexts": {"target": ["Coverage 100 Gate"], "required_now": ["Coverage 100 Gate"], "always": ["Coverage 100 Gate"], "pull_request_only": []},
            "verify_command": "bash scripts/verify",
            "github_mutation_lane": "codex-private-runner",
            "codex_auth_lane": "chatgpt-account",
            "provider_ui_mode": "playwright-manual-login",
            "codex_environment": {"mode": "automatic", "verify_command": "bash scripts/verify", "auth_file": "~/.codex/auth.json", "network_profile": "unrestricted", "methods": "all", "runner_labels": ["self-hosted", "codex-trusted"]},
        }
        ratchet_findings = profile_contract_validation.validate_profile(valid_profile, active_required_contexts_fn=lambda _profile, event_name: ["Coverage 100 Gate"])
        self.assertTrue(any("issue_policy.baseline_ref is required when mode is ratchet" in item for item in ratchet_findings))

    def test_profile_normalization_helpers_cover_edge_branches(self) -> None:
        self.assertEqual(profile_coverage_normalization._normalize_source_hint("pkg.module"), "pkg/module.py")
        self.assertEqual(profile_coverage_normalization._normalize_source_hint(""), "")
        fake_match = Mock()
        fake_match.group.return_value = "pkg"
        fake_regex = Mock()
        fake_regex.finditer.return_value = [fake_match]
        with patch.object(profile_coverage_normalization, "_GCOVR_FILTER_RE", fake_regex):
            self.assertEqual(profile_coverage_normalization._extract_gcovr_hints("gcovr --filter '.*/pkg/.*'"), ["pkg/"])
        self.assertIn("src/", profile_coverage_normalization._extract_gcovr_hints("gcovr --filter '.*/src/.*'"))
        self.assertIn("src/", profile_coverage_normalization._extract_gcovr_hints('gcovr --filter ".*/src/.*"'))
        self.assertEqual(profile_normalization.infer_required_sources({"command": ""}), [])

    def test_run_coverage_gate_remaining_helpers_cover_error_branches(self) -> None:
        self.assertEqual(run_coverage_gate._combined_coverage_percent({"components": "bad"}), 100.0)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "coverage-100").mkdir()
            (repo_dir / "coverage-100" / "coverage.json").write_text("{}", encoding="utf-8")
            with patch.object(run_coverage_gate, "_run_assert_coverage_100", return_value=0):
                self.assertEqual(
                    run_coverage_gate._collect_current_coverage_payload({}, repo_dir=repo_dir, platform_dir=repo_dir),
                    {},
                )
            with patch.object(run_coverage_gate, "_run_assert_coverage_100", return_value=2):
                with self.assertRaisesRegex(RuntimeError, "coverage assertion returned unexpected exit code 2"):
                    run_coverage_gate._collect_current_coverage_payload({}, repo_dir=repo_dir, platform_dir=repo_dir)

        with patch.object(run_coverage_gate, "_github_api_token", return_value="token"), patch.object(
            run_coverage_gate, "_download_bytes", return_value=json.dumps({"workflow_runs": []}).encode("utf-8")
        ):
            with self.assertRaisesRegex(RuntimeError, "Unable to find a successful Quality Zero Platform run"):
                run_coverage_gate._load_baseline_coverage_payload({"slug": "owner/repo", "default_branch": "main"})

        with patch.object(run_coverage_gate, "_github_api_token", return_value="token"), patch.object(
            run_coverage_gate,
            "_download_bytes",
            side_effect=[
                json.dumps({"workflow_runs": [{"id": 1, "name": "Quality Zero Platform", "conclusion": "success"}]}).encode("utf-8"),
                json.dumps({"artifacts": []}).encode("utf-8"),
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "Unable to find coverage-artifacts"):
                run_coverage_gate._load_baseline_coverage_payload({"slug": "owner/repo", "default_branch": "main"})

        script_path = Path(run_coverage_gate.__file__).resolve()
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_json = Path(temp_dir) / "profile.json"
            profile_json.write_text(json.dumps({"coverage": {"command": "", "shell": "bash", "assert_mode": {"default": "evidence_only"}}}), encoding="utf-8")
            with patch.object(sys, "argv", [str(script_path), "--profile-json", str(profile_json), "--event-name", "push"]):
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(script_path), run_name="__main__")
            self.assertEqual(result.exception.code, 0)

    def test_security_helper_remaining_error_branches(self) -> None:
        parsed = security_helpers.urlparse("https://api.github.com/repos/owner/repo")
        with self.assertRaisesRegex(TypeError, "expects keyword arguments only"):
            security_helpers._read_bytes_response(parsed, "unexpected")
        with self.assertRaisesRegex(TypeError, "Unexpected _read_bytes_response parameters: extra"):
            security_helpers._read_bytes_response(parsed, headers={}, method="GET", data=None, timeout=15, extra=True)
        with self.assertRaisesRegex(TypeError, "expects keyword arguments only"):
            security_helpers.load_bytes_https("https://api.github.com/repos/owner/repo", "unexpected")
