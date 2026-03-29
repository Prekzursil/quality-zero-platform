"""Test branch gap remediation."""

from __future__ import absolute_import

import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from typing import Tuple
from urllib.parse import urlparse
from unittest.mock import patch

from scripts import security_helpers
from scripts.quality import (
    check_codacy_zero,
    check_deepscan_zero,
    check_required_checks,
)
from scripts.quality import check_sonar_zero, control_plane_admin, control_plane_vendors
from scripts.quality import (
    export_profile,
    post_pr_quality_comment,
    profile_coverage_normalization,
)
from scripts.quality import run_codex_exec, run_quality_zero_gate
from scripts.quality.coverage_paths import _coverage_source_candidates


class _NoCloseResponse:
    """Minimal response stub that leaves the in-memory payload readable."""

    def __init__(self, payload, headers=None, *, status=200, reason="OK"):
        """Handle init."""
        self._payload = payload
        self._headers = dict(headers or {})
        self.status = status
        self.reason = reason

    def read(self):
        """Return the stored payload without closing the stubbed response."""
        return self._payload

    @property
    def headers(self):
        """Handle headers."""
        return self._headers


class BranchGapRemediationTests(unittest.TestCase):
    """Pin low-risk fixes for recently added branch-gap regression helpers."""

    AUTH_TOKEN = "-".join(["auth", "token"])

    @staticmethod
    def _write_admin_repo(root: Path) -> Tuple[Path, Path]:
        """Create a minimal admin repo fixture and return its inventory paths."""
        (root / "inventory").mkdir(parents=True, exist_ok=True)
        (root / "profiles" / "repos").mkdir(parents=True, exist_ok=True)
        inventory_path = root / "inventory" / "repos.yml"
        profile_path = root / "profiles" / "repos" / "example-repo.yml"
        inventory_path.write_text(
            "version: 1\nrepos:\n"
            "  - slug: Prekzursil/example-repo\n"
            "    profile: example-repo\n"
            "    rollout: phase2-wave0\n"
            "    default_branch: main\n",
            encoding="utf-8",
        )
        profile_path.write_text(
            "slug: Prekzursil/example-repo\nstack: python-web\nrequired_contexts:\n  target:\n    - Coverage 100 Gate\n",
            encoding="utf-8",
        )
        return inventory_path, profile_path

    def test_control_plane_admin_noop_paths(self) -> None:
        """Keep admin enrollment helpers idempotent for existing values."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inventory_path, profile_path = self._write_admin_repo(root)

            request = control_plane_admin.EnrollmentRequest(
                repo_slug="Prekzursil/example-repo",
                profile_id="example-repo",
                stack="python-web",
                rollout="phase2-wave0",
                default_branch="main",
            )
            control_plane_admin.enroll_repo(repo_root=root, request=request)
            control_plane_admin.set_issue_policy(
                repo_root=root, profile_id="example-repo", mode="zero", baseline_ref=""
            )

            target_mutation = control_plane_admin.RequiredContextMutation(
                profile_id="example-repo",
                context_set="target",
                context_name="Coverage 100 Gate",
                present=True,
            )
            missing_mutation = control_plane_admin.RequiredContextMutation(
                profile_id="example-repo",
                context_set="pull_request_only",
                context_name="Missing Context",
                present=False,
            )
            control_plane_admin.set_required_context(
                repo_root=root, mutation=target_mutation
            )
            control_plane_admin.set_required_context(
                repo_root=root, mutation=missing_mutation
            )

            inventory_text = inventory_path.read_text(encoding="utf-8")
            profile_text = profile_path.read_text(encoding="utf-8")

        self.assertEqual(inventory_text.count("slug: Prekzursil/example-repo"), 1)
        self.assertNotIn("baseline_ref", profile_text)
        self.assertEqual(profile_text.count("Coverage 100 Gate"), 1)

    def test_control_plane_vendors_existing_values_and_suffix(self) -> None:
        """Preserve vendor URLs while still deriving fallback suffixes."""
        vendor = {
            "dashboard_url": "https://app.codacy.com/gh/Prekzursil/example/dashboard"
        }
        control_plane_vendors._ensure_vendor_url(
            vendor,
            "dashboard_url",
            "https://app.codacy.com/gh/Prekzursil/other/dashboard",
            allowed_host_suffixes={"codacy.com"},
        )
        self.assertEqual(
            vendor["dashboard_url"],
            "https://app.codacy.com/gh/Prekzursil/example/dashboard",
        )

        vendors = {"sonar": {"project_key": ""}}
        control_plane_vendors._finalize_sonar_vendor(vendors)
        self.assertNotIn("dashboard_url", vendors["sonar"])

        parts_vendor = {
            "sonar": {
                "project_key": "",
                "project_key_parts": ["Prekzursil", "quality-zero-platform"],
                "project_key_separator": ":",
            }
        }
        control_plane_vendors._finalize_sonar_vendor(parts_vendor)
        self.assertEqual(
            parts_vendor["sonar"]["project_key"], "Prekzursil:quality-zero-platform"
        )
        self.assertEqual(
            parts_vendor["sonar"]["dashboard_url"],
            "https://sonarcloud.io/project/overview?id=Prekzursil%3Aquality-zero-platform",
        )

        self.assertEqual(
            control_plane_vendors._provider_env_suffix("Repo---Name___Test"),
            "REPO_NAME_TEST",
        )
        self.assertIsNone(control_plane_vendors._joined_vendor_env_parts("not-a-list"))
        self.assertIsNone(
            control_plane_vendors._joined_vendor_env_parts(["", " ", "\t"])
        )

        visual_vendors = {
            "chromatic": {
                "token_secret_parts": ["CHROMATIC", "PROJECT", "TOKEN"],
                "local_env_var_parts": ["CHROMATIC", "LOCAL", "TOKEN"],
            }
        }
        control_plane_vendors._finalize_visual_vendors(
            {"visual_pair_required": True, "repo_name": "quality-zero-platform"},
            visual_vendors,
        )
        self.assertEqual(
            visual_vendors["chromatic"]["token_secret"],
            "CHROMATIC_PROJECT_TOKEN",
        )
        self.assertEqual(
            visual_vendors["chromatic"]["local_env_var"],
            "CHROMATIC_LOCAL_TOKEN",
        )
        self.assertIn("applitools", visual_vendors)

        fallback_visual_vendors = {
            "chromatic": {
                "token_secret_parts": [],
                "local_env_var_parts": ["", " "],
            }
        }
        control_plane_vendors._finalize_visual_vendors(
            {"visual_pair_required": True, "repo_name": "quality-zero-platform"},
            fallback_visual_vendors,
        )
        self.assertEqual(
            fallback_visual_vendors["chromatic"]["token_secret"],
            "CHROMATIC_PROJECT_TOKEN",
        )
        self.assertEqual(
            fallback_visual_vendors["chromatic"]["local_env_var"],
            "CHROMATIC_PROJECT_TOKEN_QUALITY_ZERO_PLATFORM",
        )

    def test_post_pr_quality_comment_create_and_update_paths(self) -> None:
        """Cover both update and create flows for PR quality comments."""
        with patch.object(
            post_pr_quality_comment,
            "_github_request",
            side_effect=[
                [
                    {"id": 1, "body": "no marker"},
                    {"id": 2, "body": f"{post_pr_quality_comment.MARKER}\nold"},
                ],
                {"id": 2},
            ],
        ) as request_mock:
            comment_id = post_pr_quality_comment.upsert_comment(
                repo="owner/repo",
                pull_request="5",
                body="new body",
                token=self.AUTH_TOKEN,
            )
        self.assertEqual(comment_id, 2)
        self.assertEqual(request_mock.call_args_list[1].kwargs["method"], "PATCH")

        with patch.object(
            post_pr_quality_comment,
            "_github_request",
            side_effect=["not-a-list", {"id": 3}],
        ) as request_mock:
            created_id = post_pr_quality_comment.upsert_comment(
                repo="owner/repo",
                pull_request="5",
                body="created body",
                token=self.AUTH_TOKEN,
            )
        self.assertEqual(created_id, 3)
        self.assertEqual(request_mock.call_args_list[1].kwargs["method"], "POST")

    def test_export_profile_main_skips_github_output_when_unset(self) -> None:
        """Skip GitHub output emission when the caller leaves it unset."""
        profile = {"profile_id": "example", "coverage": {"inputs": []}}
        with patch.object(
            export_profile,
            "_parse_args",
            return_value=Namespace(
                inventory="",
                repo_slug="Prekzursil/quality-zero-platform",
                event_name="push",
                output="",
                out_json="",
                github_output="",
            ),
        ), patch.object(
            export_profile, "load_inventory", return_value={"repos": []}
        ), patch.object(
            export_profile, "load_repo_profile", return_value=profile
        ), patch.object(
            export_profile, "active_required_contexts", return_value=[]
        ), patch.object(
            export_profile, "_write_github_output"
        ) as write_mock, patch(
            "sys.stdout", new=io.StringIO()
        ) as stdout:
            self.assertEqual(export_profile.main(), 0)

        write_mock.assert_not_called()
        self.assertEqual(json.loads(stdout.getvalue())["profile_id"], "example")

    def test_coverage_source_candidates_and_profile_hint_helpers_cover_false_branches(
        self,
    ) -> None:
        """Cover helper branches for source discovery and profile hints."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            previous = Path.cwd()
            try:
                import os

                os.chdir(root)
                self.assertEqual(
                    _coverage_source_candidates("src/main.py", ["", "src"]),
                    ["src/main.py"],
                )
            finally:
                os.chdir(previous)

        with patch.object(
            profile_coverage_normalization, "_normalize_source_hint", return_value=""
        ):
            self.assertEqual(
                profile_coverage_normalization._extract_cov_hints("--cov=scripts"), []
            )
            self.assertEqual(
                profile_coverage_normalization._extract_gcovr_hints(
                    "--filter '.*/src/.*'"
                ),
                ["src/"],
            )

    def test_run_quality_zero_gate_and_run_codex_exec_cover_remaining_branches(
        self,
    ) -> None:
        """Cover run quality zero gate and run codex exec cover remaining branches."""
        with self.assertRaises(SystemExit):
            run_quality_zero_gate._required_contexts(
                {"required_contexts": {"target": "Coverage 100 Gate"}}
            )

        args = Namespace(
            repo_dir="repo",
            prompt_file="prompt.txt",
            output_last_message="message.txt",
            sandbox="workspace-write",
            profile="",
            model="",
            config=[],
            json_log="run.json",
        )
        completed = SimpleNamespace(stdout="", stderr="warn", returncode=0)
        with patch(
            "scripts.quality.run_codex_exec._parse_args", return_value=args
        ), patch("pathlib.Path.read_text", return_value="hello"), patch(
            "scripts.quality.run_codex_exec._run_codex_exec", return_value=completed
        ), patch(
            "sys.stdout", new=io.StringIO()
        ) as stdout, patch(
            "sys.stderr", new=io.StringIO()
        ) as stderr:
            self.assertEqual(run_codex_exec.main(), 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "warn")

    def test_codacy_and_deepscan_helpers_cover_remaining_branches(self) -> None:
        """Cover codacy and deepscan helpers cover remaining branches."""
        open_issues, findings, exc = check_codacy_zero._not_found_findings(["gh"], None)
        self.assertIsNone(open_issues)
        self.assertEqual(
            findings, ["Codacy API endpoint was not found for providers: gh."]
        )
        self.assertIsNone(exc)

        with patch(
            "scripts.quality.check_codacy_zero.codacy_zero_support.query_codacy_open_issues",
            return_value=(
                None,
                ["Codacy API endpoint was not found for providers: gh."],
                RuntimeError("boom"),
            ),
        ) as query_mock:
            open_issues, findings, exc = check_codacy_zero._query_codacy_open_issues(
                check_codacy_zero.CodacyQuery(
                    "gh", "Prekzursil", "quality-zero-platform"
                ),
                "api-token",
                ["gh"],
            )
        self.assertIsNone(open_issues)
        self.assertIn("Codacy API endpoint was not found for providers: gh.", findings)
        self.assertIsInstance(exc, RuntimeError)
        query_mock.assert_called_once()

        with patch.object(
            check_deepscan_zero, "_request_json", return_value={"total": 0}
        ):
            open_issues, source_url, findings = (
                check_deepscan_zero._evaluate_open_issues_mode(
                    "https://deepscan.io/project/issues",
                    "token",
                )
            )
        self.assertEqual(open_issues, 0)
        self.assertEqual(source_url, "https://deepscan.io/project/issues")
        self.assertEqual(findings, [])

        status = check_deepscan_zero._find_github_status(
            {
                "statuses": [
                    {"context": "Other", "state": "success"},
                    {"context": "DeepScan", "state": "success"},
                ]
            },
            "DeepScan",
        )
        self.assertIsNotNone(status)
        self.assertEqual(status["context"], "DeepScan")

    def test_required_checks_and_sonar_helpers_cover_remaining_branches(self) -> None:
        """Cover required checks and sonar helpers cover remaining branches."""
        args = Namespace(
            repo="owner/repo", sha="deadbeef", timeout_seconds=1, poll_seconds=0
        )
        pending_payload = {
            "status": "fail",
            "contexts": {
                "Coverage 100 Gate": {
                    "state": "in_progress",
                    "conclusion": "",
                    "source": "check_run",
                }
            },
        }
        with patch(
            "scripts.quality.check_required_checks._collect_payload",
            return_value=pending_payload,
        ), patch(
            "scripts.quality.check_required_checks._has_in_progress_check_runs",
            return_value=True,
        ), patch(
            "scripts.quality.check_required_checks.time.time",
            side_effect=[10, 10, 12],
        ), patch(
            "scripts.quality.check_required_checks.time.sleep"
        ) as sleep_mock:
            payload = check_required_checks._wait_for_payload(
                args, ["Coverage 100 Gate"], "token"
            )
        self.assertEqual(payload, pending_payload)
        sleep_mock.assert_called_once()

        self.assertEqual(
            check_sonar_zero._build_sonar_query(
                "project", branch="main", pull_request=""
            ),
            {"projectKey": "project", "branch": "main"},
        )

    def test_security_helpers_cover_responses_without_close_method(self) -> None:
        """Cover security helpers cover responses without close method."""
        parsed = urlparse(
            "https://api.github.com/repos/Prekzursil/quality-zero-platform/status"
        )
        response = _NoCloseResponse(b'{"ok": true}', {"X-Test": "value"})
        with patch(
            "scripts.security_helpers._ValidatedTLSConnection"
        ) as connection_cls:
            connection = connection_cls.return_value
            connection.getresponse.return_value = response
            payload, headers = security_helpers._read_json_response(
                parsed,
                headers={"Accept": "application/json"},
                method="GET",
                data=None,
                timeout=15,
            )
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(headers, {"x-test": "value"})
        connection.close.assert_called_once()

        response = _NoCloseResponse(b"bytes", {"X-Test": "value"})
        with patch(
            "scripts.security_helpers._ValidatedTLSConnection"
        ) as connection_cls:
            connection = connection_cls.return_value
            connection.getresponse.return_value = response
            payload, headers = security_helpers._read_bytes_response(
                parsed,
                headers={"Accept": "application/octet-stream"},
                method="GET",
                data=None,
                timeout=15,
            )
        self.assertEqual(payload, b"bytes")
        self.assertEqual(headers, {"x-test": "value"})
        connection.close.assert_called_once()
