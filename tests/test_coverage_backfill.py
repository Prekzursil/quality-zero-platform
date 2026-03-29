"""Test coverage backfill."""

from __future__ import absolute_import

import importlib
import json
import os
import runpy
import subprocess  # nosec B404
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from scripts.quality import (
    build_admin_dashboard,
    build_quality_rollup,
    check_dependabot_alerts,
    control_plane_admin,
    post_pr_quality_comment,
)

VALID_CONTRACT_VENDORS = {
    "chromatic": {
        "status_context": "Chromatic",
        "project_name": "proj",
        "token_secret": "token",
        "local_env_var": "env",
    },
    "applitools": {
        "status_context": "Applitools",
        "project_name": "proj",
    },
}
VALID_CONTRACT_REQUIRED_CONTEXTS = {
    "target": ["Coverage 100 Gate"],
    "required_now": ["Coverage 100 Gate"],
    "always": ["Coverage 100 Gate"],
    "pull_request_only": [],
}
VALID_CODEX_ENVIRONMENT = {
    "mode": "automatic",
    "verify_command": "bash scripts/verify",
    "auth_file": "~/.codex/auth.json",
    "network_profile": "unrestricted",
    "methods": "all",
    "runner_labels": ["self-hosted", "codex-trusted"],
}


def build_valid_contract_profile() -> dict:
    """Build and return a valid contract profile."""
    return {
        "slug": "owner/repo",
        "required_secrets": [],
        "conditional_secrets": [],
        "issue_policy": {
            "mode": "ratchet",
            "pr_behavior": "introduced_only",
            "main_behavior": "absolute",
            "baseline_ref": "",
        },
        "deps": {"policy": "zero_critical", "scope": "runtime"},
        "enabled_scanners": {"coverage": True},
        "coverage": {
            "command": "echo ok",
            "inputs": [{"name": "platform"}],
            "shell": "bash",
            "assert_mode": {"default": "enforce"},
            "require_sources_mode": "infer",
        },
        "vendors": VALID_CONTRACT_VENDORS,
        "visual_pair_required": False,
        "required_contexts": VALID_CONTRACT_REQUIRED_CONTEXTS,
        "verify_command": "bash scripts/verify",
        "github_mutation_lane": "codex-private-runner",
        "codex_auth_lane": "chatgpt-account",
        "provider_ui_mode": "playwright-manual-login",
        "codex_environment": VALID_CODEX_ENVIRONMENT,
    }


class CoverageBackfillTests(unittest.TestCase):
    """Coverage Backfill Tests."""

    def _assert_control_plane_admin_dispatch(
        self,
        root: Path,
        *,
        command: str,
        handler_name: str,
        **kwargs: str,
    ):
        """Run one control-plane admin command and assert its handler is invoked."""
        parse_args = Namespace(
            repo_root=str(root),
            command=command,
            profile_id="example",
            **kwargs,
        )
        with (
            patch.object(
                control_plane_admin,
                "parse_args",
                return_value=parse_args,
            ),
            patch.object(control_plane_admin, handler_name) as handler_mock,
        ):
            self.assertEqual(control_plane_admin.main(), 0)
        handler_mock.assert_called_once()
        return handler_mock

    def test_dashboard_parse_args_fallback_write_and_module_entrypoint(self) -> None:
        """Cover dashboard parse args fallback write and module entrypoint."""
        with patch.object(
            sys, "argv", ["build_admin_dashboard.py", "--output-dir", "site"]
        ):
            args = build_admin_dashboard.parse_args()
        self.assertEqual(args.output_dir, "site")

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "site"
            build_admin_dashboard.write_dashboard(
                output_dir, {"generated_at": "now", "repo_count": 0, "repos": []}
            )
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
                with (
                    patch.object(
                        reloaded,
                        "parse_args",
                        return_value=Namespace(
                            inventory="", output_dir=str(output_dir), assets_dir=""
                        ),
                    ),
                    patch.object(
                        reloaded, "load_inventory", return_value={"repos": []}
                    ),
                    patch.object(reloaded, "write_dashboard", return_value=None),
                ):
                    self.assertEqual(reloaded.main(), 0)
            finally:
                sys.path[:] = original_sys_path

            with (
                patch.object(
                    sys, "argv", [str(script_path), "--output-dir", str(output_dir)]
                ),
                patch.dict("os.environ", {}, clear=True),
                self.assertRaises(SystemExit) as result,
            ):
                runpy.run_path(str(script_path), run_name="__main__")
            self.assertEqual(result.exception.code, 0)

    def test_quality_rollup_parse_args_and_reload_main(self) -> None:
        """Cover quality rollup parse args and reload main."""
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
            profile_path.write_text(
                json.dumps({"slug": "owner/repo", "active_required_contexts": []}),
                encoding="utf-8",
            )
            script_path = Path(build_quality_rollup.__file__).resolve()
            root_text = str(script_path.parents[2])
            original_sys_path = list(sys.path)
            try:
                sys.path[:] = [item for item in sys.path if item != root_text]
                reloaded = importlib.reload(build_quality_rollup)
                self.assertIn(root_text, sys.path)
                with (
                    patch.object(
                        reloaded,
                        "parse_args",
                        return_value=Namespace(
                            profile_json=str(profile_path),
                            repo="owner/repo",
                            sha="abc",
                            artifacts_dir=str(root),
                            out_json="quality-rollup/summary.json",
                            out_md="quality-rollup/summary.md",
                        ),
                    ),
                    patch.object(
                        reloaded,
                        "write_report",
                        return_value=0,
                    ),
                    patch.dict("os.environ", {}, clear=True),
                ):
                    self.assertEqual(reloaded.main(), 0)
            finally:
                sys.path[:] = original_sys_path

    def test_quality_rollup_module_entrypoint_from_script_path(self) -> None:
        """Cover quality rollup module entrypoint from script path."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile_path = root / "profile.json"
            profile_path.write_text(
                json.dumps({"slug": "owner/repo", "active_required_contexts": []}),
                encoding="utf-8",
            )
            script_path = Path(build_quality_rollup.__file__).resolve()
            with (
                patch.object(
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
                ),
                patch.dict("os.environ", {}, clear=True),
                self.assertRaises(SystemExit) as result,
            ):
                runpy.run_path(str(script_path), run_name="__main__")
            self.assertEqual(result.exception.code, 0)

    def test_dependabot_parse_render_invalid_payload_and_module_entrypoint(
        self,
    ) -> None:
        """Cover dependabot parse render invalid payload and module entrypoint."""
        with patch.object(
            sys, "argv", ["check_dependabot_alerts.py", "--repo", "owner/repo"]
        ):
            args = check_dependabot_alerts._parse_args()
        self.assertEqual(args.policy, "zero_critical")
        markdown = check_dependabot_alerts._render_md(
            {
                "status": "pass",
                "repo": "owner/repo",
                "open_alerts": 0,
                "policy": "zero_critical",
                "scope": "runtime",
                "timestamp_utc": "now",
                "findings": [],
            }
        )
        self.assertIn("- None", markdown)
        with (
            patch.object(
                check_dependabot_alerts,
                "load_json_https",
                return_value=({"bad": True}, {}),
            ),
            self.assertRaisesRegex(
                RuntimeError, "Unexpected Dependabot alerts payload"
            ),
        ):
            check_dependabot_alerts._request_alerts(
                "owner/repo", "token", scope="runtime"
            )

        script_path = Path(check_dependabot_alerts.__file__).resolve()
        root_text = str(script_path.parents[2])
        trimmed_sys_path = [item for item in sys.path if item != root_text]
        with (
            patch.object(sys, "argv", [str(script_path), "--repo", "owner/repo"]),
            patch.object(sys, "path", trimmed_sys_path[:]),
            patch.dict("os.environ", {}, clear=True),
            self.assertRaises(SystemExit) as result,
        ):
            runpy.run_path(str(script_path), run_name="__main__")
        self.assertEqual(result.exception.code, 1)

    def test_control_plane_admin_load_yaml_rejects_non_mapping(self) -> None:
        """Cover control plane admin load yaml rejects non mapping."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invalid = root / "invalid.yml"
            invalid.write_text("- not-a-mapping\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Expected mapping"):
                control_plane_admin._load_yaml(invalid)

    def test_control_plane_admin_script_entrypoint_restores_sys_path(self) -> None:
        """Cover control plane admin script entrypoint restores sys path."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inventory = root / "inventory"
            profiles = root / "profiles" / "repos"
            inventory.mkdir(parents=True)
            profiles.mkdir(parents=True)
            (inventory / "repos.yml").write_text(
                "version: 1\nrepos: []\n", encoding="utf-8"
            )
            script_path = Path(control_plane_admin.__file__).resolve()
            root_text = str(script_path.parents[2])
            original_sys_path = list(sys.path)
            try:
                sys.path[:] = [item for item in sys.path if item != root_text]
                importlib.reload(control_plane_admin)
                self.assertIn(root_text, sys.path)
                with (
                    patch.object(
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
                    ),
                    patch.dict("os.environ", {}, clear=True),
                    self.assertRaises(SystemExit) as result,
                ):
                    runpy.run_path(str(script_path), run_name="__main__")
                self.assertEqual(result.exception.code, 0)
            finally:
                sys.path[:] = original_sys_path

    def test_control_plane_admin_main_dispatches_mutations(self) -> None:
        """Cover control plane admin main dispatches mutations."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._assert_control_plane_admin_dispatch(
                root,
                command="set-scanner",
                handler_name="set_scanner",
                scanner="sonar",
                enabled="true",
            )
            self._assert_control_plane_admin_dispatch(
                root,
                command="set-issue-policy",
                handler_name="set_issue_policy",
                mode="ratchet",
                baseline_ref="main",
            )

    def test_post_pr_comment_request_uses_json_helper(self) -> None:
        """Cover post pr comment request uses json helper."""
        with patch.object(
            post_pr_quality_comment,
            "load_json_https",
            return_value=({"ok": True}, {}),
        ) as load_json_mock:
            payload = post_pr_quality_comment._github_request(
                "https://api.github.com/repos/owner/repo/issues/1/comments",
                "token",
                method="POST",
                data={"body": "ok"},
            )
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(load_json_mock.call_args.kwargs["method"], "POST")

    def test_post_pr_comment_runpy_entrypoint_requires_token(self) -> None:
        """Cover post pr comment runpy entrypoint requires token."""
        script_path = Path(post_pr_quality_comment.__file__).resolve()
        root_text = str(script_path.parents[2])
        trimmed_sys_path = [item for item in sys.path if item != root_text]
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown = Path(temp_dir) / "rollup.md"
            markdown.write_text("# Rollup\n", encoding="utf-8")
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        str(script_path),
                        "--repo",
                        "owner/repo",
                        "--pull-request",
                        "1",
                        "--markdown-file",
                        str(markdown),
                    ],
                ),
                patch.object(sys, "path", trimmed_sys_path[:]),
                patch.dict(
                    "os.environ",
                    {},
                    clear=True,
                ),
                self.assertRaises(SystemExit) as result,
            ):
                runpy.run_path(str(script_path), run_name="__main__")
            self.assertEqual(
                str(result.exception), "GITHUB_TOKEN or GH_TOKEN is required"
            )

    def test_post_pr_comment_subprocess_bootstraps_repo_root(self) -> None:
        """Cover post pr comment subprocess bootstraps repo root."""
        script_path = Path(post_pr_quality_comment.__file__).resolve()
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown = Path(temp_dir) / "rollup.md"
            markdown.write_text("# Rollup\n", encoding="utf-8")
            command = [
                sys.executable,
                str(script_path),
                "--repo",
                "owner/repo",
                "--pull-request",
                "1",
                "--markdown-file",
                str(markdown),
            ]
            completed = subprocess.run(  # nosec B603
                command,
                cwd=temp_dir,
                capture_output=True,
                text=True,
                check=False,
                env={
                    k: v
                    for k, v in os.environ.items()
                    if k not in {"GITHUB_TOKEN", "GH_TOKEN"}
                },
            )
        self.assertEqual(completed.returncode, 1)
        self.assertIn(
            "GITHUB_TOKEN or GH_TOKEN is required",
            completed.stderr or completed.stdout,
        )
