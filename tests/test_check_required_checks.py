from __future__ import annotations

import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.quality import check_required_checks as checks_module


class RequiredChecksTests(unittest.TestCase):
    def test_parse_args_supports_defaults(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "check_required_checks.py",
                "--repo",
                "Prekzursil/quality-zero-platform",
                "--sha",
                "abc123",
                "--required-context",
                "Coverage 100 Gate",
            ],
        ):
            args = checks_module._parse_args()

        self.assertEqual(args.repo, "Prekzursil/quality-zero-platform")
        self.assertEqual(args.sha, "abc123")
        self.assertEqual(args.required_context, ["Coverage 100 Gate"])
        self.assertEqual(args.timeout_seconds, 900)
        self.assertEqual(args.poll_seconds, 20)

    def test_api_get_uses_expected_github_request_shape(self) -> None:
        with patch.object(checks_module, "load_json_https", return_value=({"ok": True}, None)) as loader:
            payload = checks_module._api_get("Prekzursil/quality-zero-platform", "commits/abc/status", "token-123")

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(loader.call_args.args[0], "https://api.github.com/repos/Prekzursil/quality-zero-platform/commits/abc/status")
        self.assertEqual(loader.call_args.kwargs["allowed_hosts"], {"api.github.com"})
        self.assertEqual(loader.call_args.kwargs["headers"]["Authorization"], "Bearer token-123")

    def test_api_get_rejects_non_object_payloads(self) -> None:
        with patch.object(checks_module, "load_json_https", return_value=(["not-a-dict"], None)):
            with self.assertRaisesRegex(RuntimeError, "Unexpected GitHub API response payload"):
                checks_module._api_get("Prekzursil/quality-zero-platform", "commits/abc/status", "token-123")

    def test_collect_contexts_merges_check_runs_and_statuses(self) -> None:
        contexts = checks_module._collect_contexts(
            {
                "check_runs": [
                    {"name": "shared-scanner-matrix / Coverage 100 Gate", "status": "completed", "conclusion": "success"},
                    {"name": "", "status": "completed", "conclusion": "success"},
                ]
            },
            {"statuses": [{"context": "DeepScan", "state": "success"}]},
        )

        self.assertEqual(
            contexts,
            {
                "shared-scanner-matrix / Coverage 100 Gate": {
                    "state": "completed",
                    "conclusion": "success",
                    "source": "check_run",
                },
                "DeepScan": {
                    "state": "success",
                    "conclusion": "success",
                    "source": "status",
                },
            },
        )

    def test_collect_status_contexts_skips_blank_names(self) -> None:
        contexts = checks_module._collect_status_contexts({"statuses": [{"context": "", "state": "success"}]})
        self.assertEqual(contexts, {})

    def test_evaluate_accepts_reusable_workflow_suffix_matches(self) -> None:
        status, missing, failed = checks_module._evaluate(
            ["Coverage 100 Gate", "Semgrep Zero"],
            {
                "shared-scanner-matrix / Coverage 100 Gate": {
                    "state": "completed",
                    "conclusion": "success",
                    "source": "check_run",
                },
                "shared-scanner-matrix / Semgrep Zero": {
                    "state": "completed",
                    "conclusion": "success",
                    "source": "check_run",
                },
            },
        )

        self.assertEqual(status, "pass")
        self.assertEqual(missing, [])
        self.assertEqual(failed, [])

    def test_evaluate_reports_pending_and_failed_contexts(self) -> None:
        status, missing, failed = checks_module._evaluate(
            ["Coverage 100 Gate", "DeepScan", "Missing Context"],
            {
                "shared-scanner-matrix / Coverage 100 Gate": {
                    "state": "in_progress",
                    "conclusion": "",
                    "source": "check_run",
                },
                "DeepScan": {
                    "state": "failure",
                    "conclusion": "failure",
                    "source": "status",
                },
            },
        )

        self.assertEqual(status, "fail")
        self.assertEqual(missing, ["Missing Context"])
        self.assertEqual(
            failed,
            [
                "Coverage 100 Gate: status=in_progress",
                "DeepScan: state=failure",
            ],
        )

    def test_evaluate_reports_completed_check_run_failures(self) -> None:
        status, missing, failed = checks_module._evaluate(
            ["Coverage 100 Gate"],
            {
                "shared-scanner-matrix / Coverage 100 Gate": {
                    "state": "completed",
                    "conclusion": "failure",
                    "source": "check_run",
                }
            },
        )

        self.assertEqual(status, "fail")
        self.assertEqual(missing, [])
        self.assertEqual(failed, ["Coverage 100 Gate: conclusion=failure"])

    def test_evaluate_accepts_successful_status_contexts(self) -> None:
        status, missing, failed = checks_module._evaluate(
            ["DeepScan"],
            {
                "DeepScan": {
                    "state": "success",
                    "conclusion": "success",
                    "source": "status",
                }
            },
        )

        self.assertEqual(status, "pass")
        self.assertEqual(missing, [])
        self.assertEqual(failed, [])

    def test_has_in_progress_check_runs_detects_active_check_runs(self) -> None:
        self.assertTrue(
            checks_module._has_in_progress_check_runs(
                {
                    "shared-scanner-matrix / Coverage 100 Gate": {
                        "state": "in_progress",
                        "conclusion": "",
                        "source": "check_run",
                    }
                }
            )
        )
        self.assertFalse(
            checks_module._has_in_progress_check_runs(
                {
                    "DeepScan": {
                        "state": "success",
                        "conclusion": "success",
                        "source": "status",
                    }
                }
            )
        )

    def test_collect_payload_assembles_context_report(self) -> None:
        with patch.object(
            checks_module,
            "_api_get",
            side_effect=[
                {"check_runs": [{"name": "shared-scanner-matrix / Coverage 100 Gate", "status": "completed", "conclusion": "success"}]},
                {"statuses": [{"context": "DeepScan", "state": "success"}]},
            ],
        ), patch.object(checks_module, "utc_timestamp", return_value="2026-03-15T00:00:00+00:00"):
            payload = checks_module._collect_payload(
                "Prekzursil/quality-zero-platform",
                "abc123",
                ["Coverage 100 Gate", "DeepScan"],
                "token-123",
            )

        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["missing"], [])
        self.assertEqual(payload["failed"], [])
        self.assertEqual(payload["timestamp_utc"], "2026-03-15T00:00:00+00:00")

    def test_wait_for_payload_polls_until_required_contexts_pass(self) -> None:
        payloads = [
            {
                "status": "fail",
                "missing": ["Coverage 100 Gate"],
                "failed": [],
                "contexts": {
                    "shared-scanner-matrix / Coverage 100 Gate": {
                        "state": "in_progress",
                        "conclusion": "",
                        "source": "check_run",
                    }
                },
            },
            {
                "status": "pass",
                "missing": [],
                "failed": [],
                "contexts": {
                    "shared-scanner-matrix / Coverage 100 Gate": {
                        "state": "completed",
                        "conclusion": "success",
                        "source": "check_run",
                    }
                },
            },
        ]

        with patch.object(checks_module, "_collect_payload", side_effect=payloads) as collector, patch.object(
            checks_module.time, "sleep"
        ) as sleep_mock, patch.object(checks_module.time, "time", side_effect=[0, 1, 2]):
            payload = checks_module._wait_for_payload(
                type(
                    "Args",
                    (),
                    {
                        "repo": "Prekzursil/quality-zero-platform",
                        "sha": "abc123",
                        "timeout_seconds": 60,
                        "poll_seconds": 5,
                    },
                )(),
                ["Coverage 100 Gate"],
                "token-123",
            )

        self.assertEqual(payload["status"], "pass")
        self.assertEqual(collector.call_count, 2)
        sleep_mock.assert_called_once_with(5)

    def test_wait_for_payload_returns_last_failure_when_checks_are_no_longer_running(self) -> None:
        payload = {
            "status": "fail",
            "missing": ["Coverage 100 Gate"],
            "failed": [],
            "contexts": {},
        }
        with patch.object(checks_module, "_collect_payload", return_value=payload), patch.object(checks_module.time, "time", side_effect=[0, 1]):
            result = checks_module._wait_for_payload(
                type(
                    "Args",
                    (),
                    {
                        "repo": "Prekzursil/quality-zero-platform",
                        "sha": "abc123",
                        "timeout_seconds": 60,
                        "poll_seconds": 5,
                    },
                )(),
                ["Coverage 100 Gate"],
                "token-123",
            )

        self.assertEqual(result, payload)

    def test_render_md_lists_missing_and_failed_contexts(self) -> None:
        markdown = checks_module._render_md(
            {
                "status": "fail",
                "repo": "Prekzursil/quality-zero-platform",
                "sha": "abc123",
                "timestamp_utc": "2026-03-15T00:00:00+00:00",
                "missing": ["Coverage 100 Gate"],
                "failed": ["DeepScan: state=failure"],
            }
        )

        self.assertIn("Coverage 100 Gate", markdown)
        self.assertIn("DeepScan: state=failure", markdown)

    def test_main_rejects_missing_required_contexts(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "check_required_checks.py",
                "--repo",
                "Prekzursil/quality-zero-platform",
                "--sha",
                "abc123",
            ],
        ), patch.dict(os.environ, {"GH_TOKEN": "token-123"}, clear=False):
            with self.assertRaisesRegex(SystemExit, "At least one --required-context is required"):
                checks_module.main()

    def test_main_rejects_missing_github_token(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "check_required_checks.py",
                "--repo",
                "Prekzursil/quality-zero-platform",
                "--sha",
                "abc123",
                "--required-context",
                "Coverage 100 Gate",
            ],
        ), patch.dict(os.environ, {"GH_TOKEN": "", "GITHUB_TOKEN": ""}, clear=False):
            with self.assertRaisesRegex(SystemExit, "GITHUB_TOKEN or GH_TOKEN is required"):
                checks_module.main()

    def test_script_entrypoint_raises_system_exit_from_main(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        sys_path = [entry for entry in sys.path if entry != str(repo_root)]
        if "" not in sys_path:
            sys_path.insert(0, "")

        with patch.object(
            sys,
            "argv",
            [
                "check_required_checks.py",
                "--repo",
                "Prekzursil/quality-zero-platform",
                "--sha",
                "abc123",
            ],
        ), patch.object(sys, "path", sys_path), patch.dict(os.environ, {"GH_TOKEN": "token-123"}, clear=False):
            with self.assertRaisesRegex(SystemExit, "At least one --required-context is required"):
                runpy.run_path(str(repo_root / "scripts" / "quality" / "check_required_checks.py"), run_name="__main__")

    def test_main_returns_success_when_report_written_and_payload_passes(self) -> None:
        payload = {
            "status": "pass",
            "repo": "Prekzursil/quality-zero-platform",
            "sha": "abc123",
            "timestamp_utc": "2026-03-15T00:00:00+00:00",
            "missing": [],
            "failed": [],
        }
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            sys,
            "argv",
            [
                "check_required_checks.py",
                "--repo",
                "Prekzursil/quality-zero-platform",
                "--sha",
                "abc123",
                "--required-context",
                "Coverage 100 Gate",
                "--out-json",
                str(Path(tmpdir) / "required-checks.json"),
                "--out-md",
                str(Path(tmpdir) / "required-checks.md"),
            ],
        ), patch.dict(os.environ, {"GH_TOKEN": "token-123"}, clear=False), patch.object(
            checks_module, "_wait_for_payload", return_value=payload
        ), patch.object(checks_module, "write_report", return_value=0) as writer:
            result = checks_module.main()

        self.assertEqual(result, 0)
        writer.assert_called_once()

    def test_main_returns_failure_when_payload_is_not_green(self) -> None:
        payload = {
            "status": "fail",
            "repo": "Prekzursil/quality-zero-platform",
            "sha": "abc123",
            "timestamp_utc": "2026-03-15T00:00:00+00:00",
            "missing": ["Coverage 100 Gate"],
            "failed": [],
        }
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            sys,
            "argv",
            [
                "check_required_checks.py",
                "--repo",
                "Prekzursil/quality-zero-platform",
                "--sha",
                "abc123",
                "--required-context",
                "Coverage 100 Gate",
                "--out-json",
                str(Path(tmpdir) / "required-checks.json"),
                "--out-md",
                str(Path(tmpdir) / "required-checks.md"),
            ],
        ), patch.dict(os.environ, {"GH_TOKEN": "token-123"}, clear=False), patch.object(
            checks_module, "_wait_for_payload", return_value=payload
        ), patch.object(checks_module, "write_report", return_value=0):
            result = checks_module.main()

        self.assertEqual(result, 1)

    def test_main_returns_write_report_exit_code_when_report_write_fails(self) -> None:
        payload = {
            "status": "pass",
            "repo": "Prekzursil/quality-zero-platform",
            "sha": "abc123",
            "timestamp_utc": "2026-03-15T00:00:00+00:00",
            "missing": [],
            "failed": [],
        }
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            sys,
            "argv",
            [
                "check_required_checks.py",
                "--repo",
                "Prekzursil/quality-zero-platform",
                "--sha",
                "abc123",
                "--required-context",
                "Coverage 100 Gate",
                "--out-json",
                str(Path(tmpdir) / "required-checks.json"),
                "--out-md",
                str(Path(tmpdir) / "required-checks.md"),
            ],
        ), patch.dict(os.environ, {"GH_TOKEN": "token-123"}, clear=False), patch.object(
            checks_module, "_wait_for_payload", return_value=payload
        ), patch.object(checks_module, "write_report", return_value=7):
            result = checks_module.main()

        self.assertEqual(result, 7)
