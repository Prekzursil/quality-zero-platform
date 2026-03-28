from __future__ import absolute_import

import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List, Mapping, Tuple, cast
from unittest.mock import MagicMock, patch

from scripts.quality import check_required_checks as checks_module


class RequiredChecksTests(unittest.TestCase):
    """Cover required-context gate behavior and report rendering."""

    @staticmethod
    def _wait_args():
        """Build polling arguments for the reusable wait helper tests."""
        return type(
            "Args",
            (),
            {
                "repo": "Prekzursil/quality-zero-platform",
                "sha": "abc123",
                "timeout_seconds": 60,
                "poll_seconds": 5,
            },
        )()

    @staticmethod
    def _success_check_run(
        name: str = "shared-scanner-matrix / Coverage 100 Gate",
    ) -> Mapping[str, Mapping[str, str]]:
        """Return a successful check-run payload keyed by check name."""
        return {
            name: {
                "state": "completed",
                "conclusion": "success",
                "source": "check_run",
            }
        }

    @staticmethod
    def _run_main_with_payload(
        *,
        payload: Mapping[str, object],
        write_report_result: int = 0,
    ) -> Tuple[int, MagicMock]:
        """Execute the gate entrypoint with a mocked payload and writer."""
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
        ), patch.dict(
            os.environ,
            {"GH_TOKEN": "token-123"},
            clear=False,
        ), patch.object(
            checks_module,
            "_wait_for_payload",
            return_value=payload,
        ), patch.object(
            checks_module,
            "write_report",
            return_value=write_report_result,
        ) as writer:
            result = checks_module.main()
        return result, cast(MagicMock, writer)

    def _assert_entrypoint_requires_contexts(
        self,
        *,
        repo_root: Path,
        sys_path: List[str],
    ) -> None:
        """Assert the CLI exits when no required contexts are configured."""
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
        ), patch.object(
            sys,
            "path",
            sys_path,
        ), patch.dict(
            os.environ,
            {"GH_TOKEN": "token-123"},
            clear=False,
        ):
            with self.assertRaisesRegex(
                SystemExit,
                "At least one --required-context is required",
            ):
                runpy.run_path(
                    str(
                        repo_root
                        / "scripts"
                        / "quality"
                        / "check_required_checks.py"
                    ),
                    run_name="__main__",
                )

    def test_parse_args_supports_defaults(self) -> None:
        """Verify the parser keeps the documented timeout defaults."""
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
        """Ensure GitHub API calls use the expected request envelope."""
        with patch.object(
            checks_module,
            "load_json_https",
            return_value=({"ok": True}, None),
        ) as loader:
            payload = checks_module._api_get(
                "Prekzursil/quality-zero-platform",
                "commits/abc/status",
                "token-123",
            )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(
            loader.call_args.args[0],
            (
                "https://api.github.com/repos/Prekzursil/"
                "quality-zero-platform/commits/abc/status"
            ),
        )
        self.assertEqual(loader.call_args.kwargs["allowed_hosts"], {"api.github.com"})
        self.assertEqual(
            loader.call_args.kwargs["headers"]["Authorization"],
            "Bearer token-123",
        )

    def test_api_get_rejects_non_object_payloads(self) -> None:
        """Reject GitHub API payloads that are not JSON objects."""
        with patch.object(
            checks_module,
            "load_json_https",
            return_value=(["not-a-dict"], None),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Unexpected GitHub API response payload",
            ):
                checks_module._api_get(
                    "Prekzursil/quality-zero-platform",
                    "commits/abc/status",
                    "token-123",
                )

    def test_collect_contexts_merges_check_runs_and_statuses(self) -> None:
        """Merge check-run and status contexts into a single report mapping."""
        contexts = checks_module._collect_contexts(
            {
                "check_runs": [
                    {
                        "name": "shared-scanner-matrix / Coverage 100 Gate",
                        "status": "completed",
                        "conclusion": "success",
                    },
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
        """Ignore status entries that do not declare a context name."""
        contexts = checks_module._collect_status_contexts(
            {"statuses": [{"context": "", "state": "success"}]}
        )
        self.assertEqual(contexts, {})

    def test_evaluate_accepts_reusable_workflow_suffix_matches(self) -> None:
        """Treat reusable workflow check names as satisfying required suffixes."""
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
        """Report both missing contexts and non-success check conclusions."""
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
        """Surface completed check runs that finished with a failure conclusion."""
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
        """Accept successful legacy status contexts as passing requirements."""
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
        """Flag required check runs that are still in progress."""
        self.assertTrue(
            checks_module._has_in_progress_check_runs(
                ["Coverage 100 Gate"],
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
                ["DeepScan"],
                {
                    "DeepScan": {
                        "state": "success",
                        "conclusion": "success",
                        "source": "status",
                    }
                }
            )
        )

    def test_has_in_progress_check_runs_ignores_non_required_in_progress_checks(
        self,
    ) -> None:
        """Ignore unrelated in-progress checks when required ones already passed."""
        self.assertFalse(
            checks_module._has_in_progress_check_runs(
                ["build-test"],
                {
                    "aggregate-gate / Quality Zero Gate": {
                        "state": "in_progress",
                        "conclusion": "",
                        "source": "check_run",
                    },
                    "build-test": {
                        "state": "completed",
                        "conclusion": "success",
                        "source": "check_run",
                    },
                },
            )
        )

    def test_collect_payload_assembles_context_report(self) -> None:
        """Build a normalized payload that includes merged required-check status."""
        with patch.object(
            checks_module,
            "_api_get",
            side_effect=[
                {
                    "check_runs": [
                        {
                            "name": "shared-scanner-matrix / Coverage 100 Gate",
                            "status": "completed",
                            "conclusion": "success",
                        }
                    ]
                },
                {"statuses": [{"context": "DeepScan", "state": "success"}]},
            ],
        ), patch.object(
            checks_module,
            "utc_timestamp",
            return_value="2026-03-15T00:00:00+00:00",
        ):
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
        """Keep polling until required checks move from pending to passing."""
        payloads = [
            {
                "status": "fail",
                "missing": ["Coverage 100 Gate"],
                "failed": [],
                "contexts": {
                    **self._success_check_run(),
                    "shared-scanner-matrix / Coverage 100 Gate": {
                        "state": "in_progress",
                        "conclusion": "",
                        "source": "check_run",
                    },
                },
            },
            {
                "status": "pass",
                "missing": [],
                "failed": [],
                "contexts": self._success_check_run(),
            },
        ]

        with patch.object(
            checks_module,
            "_collect_payload",
            side_effect=payloads,
        ) as collector, patch.object(
            checks_module.time,
            "sleep",
        ) as sleep_mock, patch.object(
            checks_module.time,
            "time",
            side_effect=[0, 1, 2],
        ):
            payload = checks_module._wait_for_payload(
                self._wait_args(),
                ["Coverage 100 Gate"],
                "token-123",
            )

        self.assertEqual(payload["status"], "pass")
        self.assertEqual(collector.call_count, 2)
        sleep_mock.assert_called_once_with(5)

    def test_wait_for_payload_keeps_polling_while_required_contexts_are_still_missing(
        self,
    ) -> None:
        """Continue polling when required contexts have not appeared yet."""
        payloads = [
            {
                "status": "fail",
                "missing": ["SonarCloud Code Analysis"],
                "failed": [],
                "contexts": self._success_check_run(),
            },
            {
                "status": "pass",
                "missing": [],
                "failed": [],
                "contexts": {
                    **self._success_check_run(),
                    "SonarCloud Code Analysis": {
                        "state": "success",
                        "conclusion": "success",
                        "source": "status",
                    },
                },
            },
        ]

        with patch.object(
            checks_module,
            "_collect_payload",
            side_effect=payloads,
        ) as collector, patch.object(
            checks_module.time,
            "sleep",
        ) as sleep_mock, patch.object(
            checks_module.time,
            "time",
            side_effect=[0, 1, 2],
        ):
            payload = checks_module._wait_for_payload(
                self._wait_args(),
                ["Coverage 100 Gate", "SonarCloud Code Analysis"],
                "token-123",
            )

        self.assertEqual(payload["status"], "pass")
        self.assertEqual(collector.call_count, 2)
        sleep_mock.assert_called_once_with(5)

    def test_wait_for_payload_returns_last_failure_when_checks_are_no_longer_running(
        self,
    ) -> None:
        """Return the last failing payload once required checks stop changing."""
        payload = {
            "status": "fail",
            "missing": [],
            "failed": ["Coverage 100 Gate: conclusion=failure"],
            "contexts": {
                "shared-scanner-matrix / Coverage 100 Gate": {
                    "state": "completed",
                    "conclusion": "failure",
                    "source": "check_run",
                }
            },
        }
        with patch.object(
            checks_module,
            "_collect_payload",
            return_value=payload,
        ), patch.object(checks_module.time, "time", side_effect=[0, 1]):
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
        """Render markdown output with both missing and failing context details."""
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
        """Require at least one check context before the CLI can run."""
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
            with self.assertRaisesRegex(
                SystemExit,
                "At least one --required-context is required",
            ):
                checks_module.main()

    def test_main_rejects_missing_github_token(self) -> None:
        """Exit when neither GitHub token environment variable is populated."""
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
            with self.assertRaisesRegex(
                SystemExit,
                "GITHUB_TOKEN or GH_TOKEN is required",
            ):
                checks_module.main()

    def test_script_entrypoint_raises_system_exit_from_main(self) -> None:
        """Propagate the CLI validation error through the script entrypoint."""
        repo_root = Path(__file__).resolve().parents[1]
        without_empty = [
            entry
            for entry in sys.path
            if entry != str(repo_root) and entry != ""
        ]

        for sys_path in (without_empty, ["", *without_empty]):
            if "" not in sys_path:
                sys_path.insert(0, "")
            self._assert_entrypoint_requires_contexts(
                repo_root=repo_root,
                sys_path=sys_path,
            )

    def test_script_entrypoint_uses_existing_empty_sys_path_entry(self) -> None:
        """Reuse an existing empty sys.path entry when bootstrapping the script."""
        repo_root = Path(__file__).resolve().parents[1]
        sys_path = [
            "",
            *(entry for entry in sys.path if entry != str(repo_root) and entry != ""),
        ]

        self._assert_entrypoint_requires_contexts(
            repo_root=repo_root,
            sys_path=sys_path,
        )

    def test_main_returns_success_when_report_written_and_payload_passes(self) -> None:
        """Return zero when the payload passes and report writing succeeds."""
        payload = {
            "status": "pass",
            "repo": "Prekzursil/quality-zero-platform",
            "sha": "abc123",
            "timestamp_utc": "2026-03-15T00:00:00+00:00",
            "missing": [],
            "failed": [],
        }
        result, writer = self._run_main_with_payload(payload=payload)

        self.assertEqual(result, 0)
        writer.assert_called_once()

    def test_main_returns_failure_when_payload_is_not_green(self) -> None:
        """Return one when the collected payload still contains failures."""
        payload = {
            "status": "fail",
            "repo": "Prekzursil/quality-zero-platform",
            "sha": "abc123",
            "timestamp_utc": "2026-03-15T00:00:00+00:00",
            "missing": ["Coverage 100 Gate"],
            "failed": [],
        }
        result, _writer = self._run_main_with_payload(payload=payload)

        self.assertEqual(result, 1)

    def test_main_returns_write_report_exit_code_when_report_write_fails(self) -> None:
        """Bubble up a non-zero report writer exit code to the CLI caller."""
        payload = {
            "status": "pass",
            "repo": "Prekzursil/quality-zero-platform",
            "sha": "abc123",
            "timestamp_utc": "2026-03-15T00:00:00+00:00",
            "missing": [],
            "failed": [],
        }
        result, _writer = self._run_main_with_payload(
            payload=payload,
            write_report_result=7,
        )

        self.assertEqual(result, 7)
