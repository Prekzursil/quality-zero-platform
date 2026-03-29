"""Test quality rollup extra."""

from __future__ import absolute_import

import json
import tempfile
import unittest
from argparse import Namespace
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from scripts.quality import build_quality_rollup, post_pr_quality_comment
from tests.test_quality_rollup import (
    exercise_wait_for_contexts,
    pending_then_success_contexts,
)

FAKE_GITHUB_CREDENTIAL = "gh-auth-placeholder"


class QualityRollupExtraTests(unittest.TestCase):
    """Quality Rollup Extra Tests."""

    @staticmethod
    def _context_fixture():
        """Return one mixed set of check-run and status contexts."""
        return {
            "Coverage 100 Gate": {
                "state": "completed",
                "conclusion": "success",
                "source": "check_run",
            },
            "DeepSource Visible Zero": {
                "state": "completed",
                "conclusion": "success",
                "source": "check_run",
            },
            "QLTY Zero": {
                "state": "completed",
                "conclusion": "failure",
                "source": "check_run",
            },
            "Pending": {
                "state": "in_progress",
                "conclusion": "",
                "source": "check_run",
            },
            "StatusPending": {
                "state": "pending",
                "conclusion": "pending",
                "source": "status",
            },
        }

    @staticmethod
    def _rollup_main_args(root: Path, profile_path: Path) -> Namespace:
        """Build the common CLI namespace for rollup-main tests."""
        return Namespace(
            profile_json=str(profile_path),
            repo="owner/repo",
            sha="abc123",
            artifacts_dir=str(root),
            out_json="quality-rollup/summary.json",
            out_md="quality-rollup/summary.md",
        )

    def test_parse_args_uses_default_output_paths(self) -> None:
        """Cover rollup parse-args defaults."""
        with patch(
            "sys.argv",
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
        self.assertEqual(args.out_json, build_quality_rollup.DEFAULT_ROLLUP_JSON)
        self.assertEqual(args.out_md, build_quality_rollup.DEFAULT_ROLLUP_MD)

    def test_status_and_lane_detail_helpers_cover_default_paths(self) -> None:
        """Cover status resolution and lane-detail helpers."""
        contexts = self._context_fixture()
        self.assertEqual(
            build_quality_rollup._status_from_context("Missing", contexts), "missing"
        )
        self.assertEqual(
            build_quality_rollup._status_from_context("Pending", contexts), "pending"
        )
        self.assertEqual(
            build_quality_rollup._status_from_context("StatusPending", contexts),
            "pending",
        )
        self.assertEqual(
            build_quality_rollup._status_from_context("QLTY Zero", contexts), "fail"
        )
        self.assertEqual(
            build_quality_rollup._status_from_context("Coverage 100 Gate", contexts),
            "pass",
        )
        self.assertEqual(
            build_quality_rollup._status_from_context(
                "DeepSource Visible Zero", contexts
            ),
            "pass",
        )
        self.assertEqual(
            build_quality_rollup._lane_detail({"open_issues": 2}), "Open issues: 2"
        )
        self.assertEqual(
            build_quality_rollup._lane_detail({"quality_gate": "OK"}),
            "Quality gate: OK",
        )
        self.assertEqual(
            build_quality_rollup._lane_detail({"mode": "audit"}), "Mode: audit"
        )

    def test_github_payload_accepts_check_runs_and_rejects_invalid_payloads(
        self,
    ) -> None:
        """Cover GitHub payload parsing paths."""
        with patch.object(
            build_quality_rollup,
            "load_json_https",
            return_value=(
                {
                    "check_runs": [
                        {
                            "name": "Coverage 100 Gate",
                            "status": "completed",
                            "conclusion": "success",
                        },
                        {"name": "", "status": "completed", "conclusion": "success"},
                    ]
                },
                {},
            ),
        ):
            payload = build_quality_rollup._github_payload("owner/repo", "sha", "token")
        self.assertIn("check_runs", payload)
        with (
            patch.object(
                build_quality_rollup, "load_json_https", return_value=(["invalid"], {})
            ),
            self.assertRaisesRegex(
                RuntimeError, "Unexpected GitHub API response payload"
            ),
        ):
            build_quality_rollup._github_payload("owner/repo", "sha", "token")

    def test_load_check_contexts_merges_status_payloads(self) -> None:
        """Cover load_check_contexts with mixed response types."""
        responses = [
            {
                "check_runs": [
                    {
                        "name": "Coverage 100 Gate",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]
            },
            {"statuses": [{"context": "DeepScan", "state": "success"}]},
        ]
        with patch.object(
            build_quality_rollup, "_github_payload", side_effect=responses
        ):
            contexts = build_quality_rollup.load_check_contexts(
                "owner/repo", "sha", "token"
            )
        self.assertEqual(contexts["Coverage 100 Gate"]["conclusion"], "success")
        self.assertEqual(contexts["DeepScan"]["source"], "status")
        self.assertNotIn("", contexts)

    def test_quality_rollup_main_passes_with_token_and_lane_payloads(self) -> None:
        """Cover the successful quality-rollup main path."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile_path = root / "profile.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "slug": "owner/repo",
                        "active_required_contexts": ["Coverage 100 Gate"],
                    }
                ),
                encoding="utf-8",
            )
            args = self._rollup_main_args(root, profile_path)
            with (
                patch.object(build_quality_rollup, "parse_args", return_value=args),
                patch.object(
                    build_quality_rollup,
                    "_wait_for_contexts",
                    return_value={
                        "Coverage 100 Gate": {
                            "state": "completed",
                            "conclusion": "success",
                            "source": "check_run",
                        }
                    },
                ),
                patch.object(
                    build_quality_rollup,
                    "load_lane_payloads",
                    return_value={"coverage": {"status": "pass", "findings": []}},
                ),
                patch.object(
                    build_quality_rollup, "write_report", return_value=0
                ) as write_report_mock,
                patch.dict("os.environ", {"GITHUB_TOKEN": "token"}, clear=False),
            ):
                self.assertEqual(build_quality_rollup.main(), 0)
        self.assertEqual(write_report_mock.call_args.args[0]["status"], "pass")

    def test_build_rollup_returns_pending_for_in_progress_contexts(self) -> None:
        """Cover pending rollup status when required contexts are still running."""
        profile = {
            "slug": "owner/repo",
            "active_required_contexts": ["Coverage 100 Gate"],
        }
        pending_contexts = {
            "Coverage 100 Gate": {
                "state": "in_progress",
                "conclusion": "",
                "source": "check_run",
            }
        }
        rollup = build_quality_rollup.build_rollup(
            profile=profile, lane_payloads={}, contexts=pending_contexts, sha="abc"
        )
        self.assertEqual(rollup["status"], "pending")

    def test_quality_rollup_waits_for_pending_contexts(self) -> None:
        """Cover wait_for_contexts using the shared helper sequence."""
        contexts, sleep_mock = exercise_wait_for_contexts(
            pending_then_success_contexts()
        )
        self.assertEqual(contexts["Coverage 100 Gate"]["conclusion"], "success")
        sleep_mock.assert_called_once()

    def test_quality_rollup_main_propagates_write_report_failures(self) -> None:
        """Cover main when report writing fails after a successful rollup."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile_path = root / "profile.json"
            profile_path.write_text(
                json.dumps({"slug": "owner/repo", "active_required_contexts": []}),
                encoding="utf-8",
            )
            args = Namespace(
                profile_json=str(profile_path),
                repo="owner/repo",
                sha="abc123",
                artifacts_dir=str(root),
                out_json="quality-rollup/summary.json",
                out_md="quality-rollup/summary.md",
            )
            with (
                patch.object(build_quality_rollup, "parse_args", return_value=args),
                patch.object(
                    build_quality_rollup, "load_lane_payloads", return_value={}
                ),
                patch.object(build_quality_rollup, "write_report", return_value=4),
                patch.dict("os.environ", {}, clear=True),
            ):
                self.assertEqual(build_quality_rollup.main(), 4)

    def test_comment_client_updates_existing_comment_or_creates_one(self) -> None:
        """Cover comment client updates existing comment or creates one."""
        with patch.object(
            post_pr_quality_comment,
            "_github_request",
            side_effect=[
                [{"id": 7, "body": "<!-- quality-zero-rollup --> old"}],
                {"id": 7},
            ],
        ) as request_mock:
            self.assertEqual(
                post_pr_quality_comment.upsert_comment(
                    repo="owner/repo",
                    pull_request="12",
                    body="<!-- quality-zero-rollup -->\nnew",
                    token=FAKE_GITHUB_CREDENTIAL,
                ),
                7,
            )
        self.assertEqual(request_mock.call_args_list[1].kwargs["method"], "PATCH")

        with patch.object(
            post_pr_quality_comment,
            "_github_request",
            side_effect=[
                [],
                {"id": 11},
            ],
        ) as request_mock:
            self.assertEqual(
                post_pr_quality_comment.upsert_comment(
                    repo="owner/repo",
                    pull_request="12",
                    body="<!-- quality-zero-rollup -->\nnew",
                    token=FAKE_GITHUB_CREDENTIAL,
                ),
                11,
            )
        self.assertEqual(request_mock.call_args_list[1].kwargs["method"], "POST")

    def test_post_pr_comment_parse_args_and_missing_token_path(self) -> None:
        """Cover post pr comment parse args and missing token path."""
        with patch(
            "sys.argv",
            [
                "post_pr_quality_comment.py",
                "--repo",
                "owner/repo",
                "--pull-request",
                "12",
                "--markdown-file",
                "note.md",
            ],
        ):
            args = post_pr_quality_comment.parse_args()
        self.assertEqual(args.repo, "owner/repo")

        with tempfile.TemporaryDirectory() as temp_dir:
            markdown = Path(temp_dir) / "note.md"
            markdown.write_text("# Rollup\n", encoding="utf-8")
            args = Namespace(
                repo="owner/repo", pull_request="12", markdown_file=str(markdown)
            )

            with (
                patch.object(post_pr_quality_comment, "parse_args", return_value=args),
                patch.dict("os.environ", {}, clear=True),
                self.assertRaises(SystemExit) as exc,
            ):
                post_pr_quality_comment.main()
            self.assertEqual(str(exc.exception), "GITHUB_TOKEN or GH_TOKEN is required")

    def test_post_pr_comment_main_wraps_errors(self) -> None:
        """Cover post pr comment main wraps errors."""
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown = Path(temp_dir) / "note.md"
            markdown.write_text("# Rollup\n", encoding="utf-8")
            args = Namespace(
                repo="owner/repo", pull_request="12", markdown_file=str(markdown)
            )

            with (
                patch.object(
                    post_pr_quality_comment,
                    "parse_args",
                    return_value=args,
                ),
                patch.dict(
                    "os.environ",
                    {"GITHUB_TOKEN": FAKE_GITHUB_CREDENTIAL},
                    clear=False,
                ),
                patch.object(
                    post_pr_quality_comment,
                    "upsert_comment",
                    side_effect=RuntimeError("boom"),
                ),
                self.assertRaises(SystemExit) as exc,
            ):
                post_pr_quality_comment.main()
            self.assertIn("Unable to post PR comment: boom", str(exc.exception))

    def test_post_pr_comment_main_wraps_http_errors(self) -> None:
        """Cover post pr comment main wraps HTTP errors."""
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown = Path(temp_dir) / "note.md"
            markdown.write_text("# Rollup\n", encoding="utf-8")
            args = Namespace(
                repo="owner/repo", pull_request="12", markdown_file=str(markdown)
            )
            http_error = HTTPError(
                url="https://api.github.com/repos/owner/repo/issues/comments",
                code=502,
                msg="Bad Gateway",
                hdrs=Message(),
                fp=None,
            )

            with (
                patch.object(
                    post_pr_quality_comment,
                    "parse_args",
                    return_value=args,
                ),
                patch.dict(
                    "os.environ",
                    {"GITHUB_TOKEN": FAKE_GITHUB_CREDENTIAL},
                    clear=False,
                ),
                patch.object(
                    post_pr_quality_comment,
                    "upsert_comment",
                    side_effect=http_error,
                ),
                self.assertRaises(SystemExit) as exc,
            ):
                post_pr_quality_comment.main()
            self.assertIn(
                "Unable to post PR comment: HTTP Error 502",
                str(exc.exception),
            )

    def test_post_pr_comment_main_returns_zero_on_success(self) -> None:
        """Cover post pr comment main returns zero on success."""
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown = Path(temp_dir) / "note.md"
            markdown.write_text("# Rollup\n", encoding="utf-8")
            args = Namespace(
                repo="owner/repo", pull_request="12", markdown_file=str(markdown)
            )

            with (
                patch.object(
                    post_pr_quality_comment,
                    "parse_args",
                    return_value=args,
                ),
                patch.dict(
                    "os.environ",
                    {"GITHUB_TOKEN": FAKE_GITHUB_CREDENTIAL},
                    clear=False,
                ),
                patch.object(
                    post_pr_quality_comment,
                    "upsert_comment",
                    return_value=5,
                ),
            ):
                self.assertEqual(post_pr_quality_comment.main(), 0)
