"""Test check sentry zero."""

from __future__ import absolute_import

import importlib.util
import os
import runpy
import sys
import tempfile
import unittest
from argparse import Namespace
from email.message import Message
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import cast
from unittest.mock import patch
from urllib.error import HTTPError

from scripts.quality import check_sentry_zero as sentry_module


class SentryZeroTests(unittest.TestCase):
    """Exercise the Sentry zero gate across parsing, reporting, and error paths."""

    def test_spec_import_bootstraps_repo_root_on_sys_path(self) -> None:
        """Load the module from its file path to cover repo-root bootstrapping."""
        module_path = Path(sentry_module.__file__).resolve()
        repo_root = str(module_path.parents[2])
        spec = importlib.util.spec_from_file_location(
            "check_sentry_zero_bootstrap",
            module_path,
        )
        if spec is None or spec.loader is None:  # pragma: no cover
            self.fail("Expected a concrete module spec for check_sentry_zero")
        module = importlib.util.module_from_spec(cast(ModuleSpec, spec))
        original_path = list(sys.path)
        sys.path = [entry for entry in sys.path if entry != repo_root]
        try:
            spec.loader.exec_module(module)
            self.assertIn(repo_root, sys.path)
        finally:
            sys.path = original_path

    def test_parse_args_uses_sentry_gate_defaults(self) -> None:
        """Parse the CLI arguments that drive the Sentry gate."""
        with patch.object(
            sys,
            "argv",
            [
                "check_sentry_zero.py",
                "--org",
                "prekzursil",
                "--project",
                "quality-zero-platform",
                "--token",
                "token-123",
            ],
        ):
            args = sentry_module._parse_args()

        self.assertEqual(args.org, "prekzursil")
        self.assertEqual(args.project, ["quality-zero-platform"])
        self.assertEqual(args.token, "token-123")
        self.assertEqual(args.out_json, "sentry-zero/sentry.json")
        self.assertEqual(args.out_md, "sentry-zero/sentry.md")

    def test_request_json_uses_expected_sentry_request_shape(self) -> None:
        """Send Sentry API requests through the shared HTTPS helper."""
        with patch.object(
            sentry_module,
            "load_json_https",
            return_value=(["payload"], {"x-hits": "7"}),
        ) as loader:
            payload, headers = sentry_module._request_json(
                "https://sentry.io/api/0/projects/org/app/issues/",
                "token-123",
            )

        self.assertEqual(payload, ["payload"])
        self.assertEqual(headers, {"x-hits": "7"})
        self.assertEqual(
            loader.call_args.args[0],
            "https://sentry.io/api/0/projects/org/app/issues/",
        )
        self.assertEqual(
            loader.call_args.kwargs["allowed_host_suffixes"],
            {"sentry.io"},
        )
        self.assertEqual(
            loader.call_args.kwargs["headers"]["Authorization"],
            "Bearer token-123",
        )

    def test_hits_from_headers_handles_valid_invalid_and_missing_values(self) -> None:
        """Cover the numeric, invalid, and missing x-hits header branches."""
        self.assertEqual(sentry_module._hits_from_headers({"x-hits": "7"}), 7)
        self.assertIsNone(sentry_module._hits_from_headers({"x-hits": "bad"}))
        self.assertIsNone(sentry_module._hits_from_headers({}))

    def test_collect_projects_includes_environment_aliases_without_duplicates(
        self,
    ) -> None:
        """Collect project names from arguments and the Sentry environment aliases."""
        with patch.dict(
            os.environ,
            {
                "SENTRY_PROJECT": "quality-zero-platform;event-link",
                "SENTRY_PROJECT_BACKEND": "event-link-backend",
                "SENTRY_PROJECT_WEB": "event-link-web\nevent-link",
            },
            clear=False,
        ):
            projects = sentry_module._collect_projects(
                ["quality-zero-platform", "quality-zero-platform"]
            )

        self.assertEqual(
            projects,
            [
                "quality-zero-platform",
                "event-link",
                "event-link-backend",
                "event-link-web",
            ],
        )

    def test_validate_sentry_inputs_reports_missing_configuration(self) -> None:
        """Return all missing-input findings for an empty Sentry configuration."""
        self.assertEqual(
            sentry_module._validate_sentry_inputs("", "", []),
            [
                "SENTRY_AUTH_TOKEN is missing.",
                "SENTRY_ORG is missing.",
                "No Sentry projects configured.",
            ],
        )

    def test_issues_url_and_render_md_cover_empty_state_reporting(self) -> None:
        """Render the zero-findings markdown shape for empty project results."""
        self.assertEqual(
            sentry_module._issues_url("prek/zursil", "event link"),
            (
                "https://sentry.io/api/0/projects/prek%2Fzursil/"
                "event%20link/issues/?query=is%3Aunresolved&limit=1"
                "&project=event%2520link"
            ),
        )
        markdown = sentry_module._render_md(
            {
                "status": "fail",
                "org": "prekzursil",
                "timestamp_utc": "2026-03-28T00:00:00+00:00",
                "projects": [],
                "findings": [],
            }
        )
        self.assertIn("## Project results", markdown)
        self.assertIn("## Findings", markdown)
        self.assertGreaterEqual(markdown.count("- None"), 2)

    def test_collect_project_results_marks_missing_projects_as_not_found(self) -> None:
        """Treat missing Sentry projects as a non-blocking configuration gap."""
        with patch.object(
            sentry_module,
            "_request_json",
            side_effect=HTTPError(
                "https://sentry.io/api/0/projects/prekzursil/event-link/issues/",
                404,
                "Not Found",
                hdrs=Message(),
                fp=None,
            ),
        ):
            results, findings = sentry_module._collect_project_results(
                "prekzursil",
                ["event-link"],
                "token-123",
            )

        self.assertEqual(
            results,
            [{"project": "event-link", "unresolved": 0, "state": "not_found"}],
        )
        self.assertEqual(findings, [])

    def test_collect_project_results_marks_present_projects_as_ok(self) -> None:
        """Keep successful Sentry project probes visible in the markdown output."""
        with patch.object(
            sentry_module,
            "_request_json",
            return_value=([], {"x-hits": "0"}),
        ):
            results, findings = sentry_module._collect_project_results(
                "prekzursil",
                ["quality-zero-platform"],
                "token-123",
            )

        self.assertEqual(
            results,
            [
                {
                    "project": "quality-zero-platform",
                    "unresolved": 0,
                    "state": "ok",
                }
            ],
        )
        self.assertEqual(findings, [])

    def test_collect_project_results_uses_payload_length_and_reports_unresolved_issues(
        self,
    ) -> None:
        """Fall back to payload length when Sentry omits the x-hits header."""
        with patch.object(
            sentry_module,
            "_request_json",
            return_value=([{"id": "a"}, {"id": "b"}], {}),
        ):
            results, findings = sentry_module._collect_project_results(
                "prekzursil",
                ["quality-zero-platform"],
                "token-123",
            )

        self.assertEqual(
            results,
            [
                {
                    "project": "quality-zero-platform",
                    "unresolved": 2,
                    "state": "ok",
                }
            ],
        )
        self.assertEqual(
            findings,
            [
                "Sentry project quality-zero-platform has 2 unresolved issues "
                "(expected 0)."
            ],
        )

    def test_collect_project_results_validates_payload_and_reraises_non_404_http_errors(
        self,
    ) -> None:
        """Reject invalid payload shapes and surface non-404 provider failures."""
        with patch.object(
            sentry_module,
            "_request_json",
            return_value=({"bad": "payload"}, {"x-hits": "0"}),
        ), self.assertRaisesRegex(
            RuntimeError,
            "Unexpected Sentry issues response payload",
        ):
            sentry_module._collect_project_results(
                "prekzursil",
                ["quality-zero-platform"],
                "token-123",
            )

        with patch.object(
            sentry_module,
            "_request_json",
            side_effect=HTTPError(
                "https://sentry.io/api/0/projects/prekzursil/app/issues/",
                500,
                "Server Error",
                hdrs=Message(),
                fp=None,
            ),
        ), self.assertRaises(HTTPError):
            sentry_module._collect_project_results(
                "prekzursil",
                ["quality-zero-platform"],
                "token-123",
            )

    def test_render_md_includes_project_state_suffixes(self) -> None:
        """Show non-default project states in the human-readable report."""
        markdown = sentry_module._render_md(
            {
                "status": "pass",
                "org": "prekzursil",
                "timestamp_utc": "2026-03-28T00:00:00+00:00",
                "projects": [
                    {
                        "project": "quality-zero-platform",
                        "unresolved": 0,
                        "state": "ok",
                    },
                    {
                        "project": "event-link",
                        "unresolved": 0,
                        "state": "not_found",
                    },
                ],
                "findings": [],
            }
        )

        self.assertIn("`quality-zero-platform` unresolved=`0`", markdown)
        self.assertIn("`event-link` unresolved=`0` state=`not_found`", markdown)

    def test_main_returns_failure_payload_for_missing_inputs(self) -> None:
        """Return a failing report when the required Sentry inputs are missing."""
        args = Namespace(
            org=str(),
            project=[],
            token=str(),
            out_json="sentry-zero/sentry.json",
            out_md="sentry-zero/sentry.md",
        )
        with patch.dict(
            "os.environ",
            {},
            clear=True,
        ), patch.object(
            sentry_module,
            "_parse_args",
            return_value=args,
        ), patch.object(
            sentry_module,
            "write_report",
            return_value=0,
        ) as write_report_mock:
            result = sentry_module.main()

        self.assertEqual(result, 1)
        missing_payload = write_report_mock.call_args.args[0]
        self.assertEqual(missing_payload["status"], "fail")
        self.assertIn("SENTRY_AUTH_TOKEN is missing.", missing_payload["findings"])
        self.assertIn("SENTRY_ORG is missing.", missing_payload["findings"])
        self.assertIn("No Sentry projects configured.", missing_payload["findings"])

    def test_main_returns_failure_payload_for_runtime_exceptions(self) -> None:
        """Convert collection-time exceptions into a failing Sentry report."""
        sentry_token = "-".join(["token", "123"])
        ok_args = Namespace(
            org="prekzursil",
            project=["quality-zero-platform"],
            token=sentry_token,
            out_json="sentry-zero/sentry.json",
            out_md="sentry-zero/sentry.md",
        )
        with patch.object(
            sentry_module,
            "_parse_args",
            return_value=ok_args,
        ), patch.object(
            sentry_module,
            "_collect_project_results",
            side_effect=RuntimeError("provider down"),
        ), patch.object(
            sentry_module,
            "write_report",
            return_value=0,
        ) as write_report_mock:
            result = sentry_module.main()

        self.assertEqual(result, 1)
        failure_payload = write_report_mock.call_args.args[0]
        self.assertEqual(failure_payload["status"], "fail")
        self.assertEqual(
            failure_payload["findings"],
            ["Sentry API request failed: provider down"],
        )

    def test_main_returns_success_for_clean_projects(self) -> None:
        """Return success when every configured Sentry project is clean."""
        sentry_token = "-".join(["token", "123"])
        args = Namespace(
            org="prekzursil",
            project=["quality-zero-platform"],
            token=sentry_token,
            out_json="sentry-zero/sentry.json",
            out_md="sentry-zero/sentry.md",
        )
        with patch.object(
            sentry_module,
            "_parse_args",
            return_value=args,
        ), patch.object(
            sentry_module,
            "_collect_project_results",
            return_value=(
                [
                    {
                        "project": "quality-zero-platform",
                        "unresolved": 0,
                        "state": "ok",
                    }
                ],
                [],
            ),
        ), patch.object(
            sentry_module,
            "write_report",
            return_value=0,
        ) as write_report_mock:
            result = sentry_module.main()

        self.assertEqual(result, 0)
        success_payload = write_report_mock.call_args.args[0]
        self.assertEqual(success_payload["status"], "pass")
        self.assertEqual(
            success_payload["projects"],
            [
                {
                    "project": "quality-zero-platform",
                    "unresolved": 0,
                    "state": "ok",
                }
            ],
        )

    def test_main_propagates_write_report_failures(self) -> None:
        """Preserve write_report failures after a successful Sentry collection."""
        sentry_token = "-".join(["token", "123"])
        args = Namespace(
            org="prekzursil",
            project=["quality-zero-platform"],
            token=sentry_token,
            out_json="sentry-zero/sentry.json",
            out_md="sentry-zero/sentry.md",
        )
        with patch.object(
            sentry_module,
            "_parse_args",
            return_value=args,
        ), patch.object(
            sentry_module,
            "_collect_project_results",
            return_value=(
                [
                    {
                        "project": "quality-zero-platform",
                        "unresolved": 0,
                        "state": "ok",
                    }
                ],
                [],
            ),
        ), patch.object(
            sentry_module,
            "write_report",
            return_value=9,
        ):
            self.assertEqual(sentry_module.main(), 9)

    def test_run_as_main_raises_system_exit(self) -> None:
        """Execute the script entrypoint to cover the __main__ guard."""
        module_path = Path(sentry_module.__file__).resolve()
        with tempfile.TemporaryDirectory(dir=str(Path.cwd())) as tmpdir, patch.object(
            sys,
            "argv",
            [
                str(module_path),
                "--out-json",
                str(Path(tmpdir) / "sentry.json"),
                "--out-md",
                str(Path(tmpdir) / "sentry.md"),
            ],
        ), patch.dict(
            os.environ,
            {},
            clear=True,
        ), self.assertRaises(
            SystemExit
        ) as exc_info:
            runpy.run_path(str(module_path), run_name="__main__")

        self.assertEqual(exc_info.exception.code, 1)
