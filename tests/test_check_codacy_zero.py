from __future__ import absolute_import

import os
import unittest
from argparse import Namespace
from email.message import Message
import runpy
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple
from unittest.mock import patch

from scripts.quality import check_codacy_zero
from scripts.quality.check_codacy_zero import (
    _build_payload,
    _provider_candidates,
    _query_codacy_open_issues,
    _query_codacy_provider,
    _resolve_codacy_status,
    _request_mode,
    build_issues_url,
    build_repository_analysis_url,
    extract_total_open,
)


class CodacyZeroTests(unittest.TestCase):
    def test_build_issues_url_uses_pull_request_endpoint_when_available(self) -> None:
        self.assertEqual(
            build_issues_url("gh", "Prekzursil", "quality-zero-platform", pull_request="5"),
            (
                "https://app.codacy.com/api/v3/analysis/organizations/gh/Prekzursil/"
                "repositories/quality-zero-platform/pull-requests/5/issues?status=new&limit=1"
            ),
        )

    def test_build_issues_url_uses_repository_endpoint_without_pull_request(self) -> None:
        self.assertEqual(
            build_issues_url("gh", "Prekzursil", "quality-zero-platform", pull_request=""),
            "https://api.codacy.com/api/v3/analysis/organizations/gh/Prekzursil/repositories/quality-zero-platform/issues/search?limit=1",
        )

    def test_extract_total_open_supports_nested_payloads(self) -> None:
        self.assertEqual(extract_total_open({"paging": {"total": 3}}), 3)
        self.assertEqual(extract_total_open([{"details": {"open_issues": 2}}]), 2)
        self.assertIsNone(extract_total_open({"items": [{"details": "no-count"}]}))

    def test_provider_candidates_and_request_mode_cover_default_paths(self) -> None:
        self.assertEqual(_provider_candidates("gh"), ["gh", "github"])
        self.assertEqual(_provider_candidates("custom"), ["custom", "gh", "github"])
        self.assertEqual(_request_mode(""), ("POST", {}))
        self.assertEqual(_request_mode("5"), ("GET", None))

    def test_request_json_rejects_non_dict_payloads(self) -> None:
        with patch("scripts.quality.check_codacy_zero.load_json_https", return_value=(["invalid"], {})):
            with self.assertRaisesRegex(RuntimeError, "Unexpected Codacy API response payload"):
                check_codacy_zero._request_json("https://api.codacy.com/test", "token")

        with patch("scripts.quality.check_codacy_zero.load_json_https", return_value=({"total": 0}, {})):
            self.assertEqual(check_codacy_zero._request_json("https://api.codacy.com/test", "token"), {"total": 0})

    def test_query_open_issues_falls_back_after_a_404_provider_probe(self) -> None:
        responses = [
            Exception("sentinel"),
            {"total": 0},
        ]

        def fake_request(url: str, token: str, *, method: str = "GET", data=None):
            current = responses.pop(0)
            if isinstance(current, Exception):
                from urllib.error import HTTPError

                headers = Message()
                raise HTTPError(url, 404, "Not Found", hdrs=headers, fp=None)
            return current

        with patch("scripts.quality.check_codacy_zero._request_json", side_effect=fake_request):
            open_issues, findings, _ = _query_codacy_open_issues(
                "Prekzursil",
                "quality-zero-platform",
                "token",
                ["custom", "gh"],
            )

        self.assertEqual(open_issues, 0)
        self.assertEqual(findings, [])

    def test_query_open_issues_uses_pull_request_get_endpoint(self) -> None:
        captured: List[Tuple[str, str, object | None]] = []

        def fake_request(url: str, token: str, *, method: str = "GET", data=None):
            captured.append((url, method, data))
            return {"total": 0}

        with patch("scripts.quality.check_codacy_zero._request_json", side_effect=fake_request):
            open_issues, findings, _ = _query_codacy_open_issues(
                "Prekzursil",
                "quality-zero-platform",
                "token",
                ["gh"],
                pull_request="5",
            )

        self.assertEqual(open_issues, 0)
        self.assertEqual(findings, [])
        expected_url = (
            "https://app.codacy.com/api/v3/analysis/organizations/gh/Prekzursil/repositories/"
            "quality-zero-platform/pull-requests/5/issues?status=new&limit=1"
        )
        self.assertEqual(captured, [(expected_url, "GET", None)])

    def test_query_codacy_provider_reports_missing_totals_and_open_issues(self) -> None:
        with patch("scripts.quality.check_codacy_zero._request_json", return_value={"items": []}):
            open_issues, findings = _query_codacy_provider("gh", "Prekzursil", "quality-zero-platform", "token")
        self.assertIsNone(open_issues)
        self.assertEqual(findings, ["Codacy response did not include a parseable total issue count."])

        with patch("scripts.quality.check_codacy_zero._request_json", return_value={"total": 2}):
            open_issues, findings = _query_codacy_provider("gh", "Prekzursil", "quality-zero-platform", "token")
        self.assertEqual(open_issues, 2)
        self.assertEqual(findings, ["Codacy reports 2 open issues (expected 0)."])

    def test_query_codacy_public_repository_issues_covers_success_and_invalid_payloads(self) -> None:
        with patch("scripts.quality.check_codacy_zero.load_json_https", return_value=({"issuesCount": 0}, {})):
            open_issues, findings = check_codacy_zero._query_codacy_public_repository_issues(
                "gh",
                "Prekzursil",
                "quality-zero-platform",
            )
        self.assertEqual(open_issues, 0)
        self.assertEqual(findings, [])

        with patch("scripts.quality.check_codacy_zero.load_json_https", return_value=(["invalid"], {})):
            with self.assertRaisesRegex(RuntimeError, "Unexpected Codacy public repository payload"):
                check_codacy_zero._query_codacy_public_repository_issues("gh", "Prekzursil", "quality-zero-platform")

    def test_query_open_issues_uses_public_repository_fallback_after_401_on_main(self) -> None:
        from urllib.error import HTTPError

        headers = Message()
        with patch(
            "scripts.quality.check_codacy_zero._query_codacy_provider",
            side_effect=HTTPError("https://api.codacy.com", 401, "Unauthorized", hdrs=headers, fp=None),
        ), patch(
            "scripts.quality.check_codacy_zero._query_codacy_public_repository_issues",
            return_value=(0, []),
        ) as fallback_mock:
            open_issues, findings, exc = _query_codacy_open_issues(
                "Prekzursil",
                "quality-zero-platform",
                "token",
                ["gh"],
            )

        self.assertEqual(open_issues, 0)
        self.assertEqual(findings, [])
        self.assertIsNone(exc)
        fallback_mock.assert_called_once_with("gh", "Prekzursil", "quality-zero-platform")

    def test_handle_codacy_http_error_covers_pull_request_and_failed_public_fallback(self) -> None:
        from urllib.error import HTTPError

        headers = Message()
        error = HTTPError("https://api.codacy.com", 401, "Unauthorized", hdrs=headers, fp=None)
        query = check_codacy_zero.CodacyQuery("gh", "Prekzursil", "quality-zero-platform", pull_request="5")
        self.assertIsNone(check_codacy_zero._fallback_public_issues(query))
        self.assertEqual(
            check_codacy_zero._handle_codacy_http_error(error, query),
            (None, ["Codacy API request failed: HTTP 401"], error, True),
        )

        with patch(
            "scripts.quality.check_codacy_zero._query_codacy_public_repository_issues",
            side_effect=RuntimeError("fallback broke"),
        ):
            open_issues, findings, exc, should_return = check_codacy_zero._handle_codacy_http_error(
                error,
                check_codacy_zero.CodacyQuery("gh", "Prekzursil", "quality-zero-platform"),
            )

        self.assertIsNone(open_issues)
        self.assertEqual(findings, [])
        self.assertIsInstance(exc, RuntimeError)
        self.assertFalse(should_return)

    def test_keyword_only_guards_reject_unexpected_arguments(self) -> None:
        with self.assertRaisesRegex(TypeError, "Unexpected _query_codacy_provider parameters: extra"):
            _query_codacy_provider(
                "gh",
                "Prekzursil",
                "quality-zero-platform",
                "token",
                extra=True,
            )

        with self.assertRaisesRegex(TypeError, "expects provider, owner, repo, and token"):
            _query_codacy_provider("gh", "Prekzursil", "quality-zero-platform")

        with self.assertRaisesRegex(TypeError, "Unexpected _query_codacy_open_issues parameters: extra"):
            _query_codacy_open_issues(
                "Prekzursil",
                "quality-zero-platform",
                "token",
                ["gh"],
                extra=True,
            )

        with self.assertRaisesRegex(TypeError, "expects owner, repo, token, and provider candidates"):
            _query_codacy_open_issues("Prekzursil", "quality-zero-platform", "token")

    def test_query_open_issues_reports_non_404_and_runtime_failures(self) -> None:
        from urllib.error import HTTPError

        headers = Message()
        with patch(
            "scripts.quality.check_codacy_zero._query_codacy_provider",
            side_effect=HTTPError("https://api.codacy.com", 500, "Boom", hdrs=headers, fp=None),
        ):
            open_issues, findings, exc = _query_codacy_open_issues(
                "Prekzursil",
                "quality-zero-platform",
                "token",
                ["gh"],
            )
        self.assertIsNone(open_issues)
        self.assertEqual(findings, ["Codacy API request failed: HTTP 500"])
        self.assertIsNotNone(exc)

        with patch(
            "scripts.quality.check_codacy_zero._query_codacy_provider",
            side_effect=RuntimeError("network broke"),
        ):
            open_issues, findings, exc = _query_codacy_open_issues(
                "Prekzursil",
                "quality-zero-platform",
                "token",
                ["gh"],
            )
        self.assertIsNone(open_issues)
        self.assertEqual(findings, ["Codacy API request failed: network broke"])
        self.assertIsInstance(exc, RuntimeError)

    def test_query_open_issues_reports_missing_provider_endpoints_after_all_404s(self) -> None:
        from urllib.error import HTTPError

        headers = Message()

        def fake_provider(*_args, **_kwargs):
            raise HTTPError("https://api.codacy.com", 404, "Not Found", hdrs=headers, fp=None)

        with patch("scripts.quality.check_codacy_zero._query_codacy_provider", side_effect=fake_provider):
            open_issues, findings, exc = _query_codacy_open_issues(
                "Prekzursil",
                "quality-zero-platform",
                "token",
                ["custom", "github"],
            )

        self.assertIsNone(open_issues)
        self.assertIn("Codacy API endpoint was not found", findings[0])
        self.assertIsNotNone(exc)

    def test_main_handles_missing_token_success_and_report_failures(self) -> None:
        args = Namespace(
            provider="gh",
            owner="Prekzursil",
            repo="quality-zero-platform",
            pull_request="",
            token=str(),
            out_json="codacy-zero/codacy.json",
            out_md="codacy-zero/codacy.md",
        )
        with patch.dict("os.environ", {}, clear=True), patch.object(check_codacy_zero, "_parse_args", return_value=args), patch.object(
            check_codacy_zero, "write_report", return_value=0
        ) as write_report_mock:
            self.assertEqual(check_codacy_zero.main(), 1)
        self.assertEqual(write_report_mock.call_args.args[0]["findings"], ["CODACY_API_TOKEN is missing."])

        success_args = Namespace(**{**args.__dict__, "token": "explicit-token", "pull_request": "5"})
        with patch.object(check_codacy_zero, "_parse_args", return_value=success_args), patch.object(
            check_codacy_zero, "_query_codacy_open_issues", return_value=(0, [], None)
        ), patch.object(check_codacy_zero, "write_report", return_value=0) as write_report_mock:
            self.assertEqual(check_codacy_zero.main(), 0)
        self.assertEqual(write_report_mock.call_args.args[0]["status"], "pass")

        with patch.object(check_codacy_zero, "_parse_args", return_value=success_args), patch.object(
            check_codacy_zero, "_query_codacy_open_issues", return_value=(0, [], None)
        ), patch.object(check_codacy_zero, "write_report", return_value=7):
            self.assertEqual(check_codacy_zero.main(), 7)

        audit_args = Namespace(**{**success_args.__dict__, "policy_mode": "audit"})
        with patch.object(check_codacy_zero, "_parse_args", return_value=audit_args), patch.object(
            check_codacy_zero, "_query_codacy_open_issues", return_value=(5, ["Codacy reports 5 open issues (expected 0)."], None)
        ), patch.object(check_codacy_zero, "write_report", return_value=0) as write_report_mock:
            self.assertEqual(check_codacy_zero.main(), 0)
        self.assertEqual(write_report_mock.call_args.args[0]["status"], "pass")

    def test_parse_args_render_markdown_and_script_entrypoint(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "check_codacy_zero.py",
                "--owner",
                "Prekzursil",
                "--repo",
                "quality-zero-platform",
            ],
        ):
            args = check_codacy_zero._parse_args()
        self.assertEqual(args.provider, "gh")
        self.assertEqual(args.out_json, "codacy-zero/codacy.json")
        markdown = check_codacy_zero._render_md(
            {
                "status": "pass",
                "owner": "Prekzursil",
                "repo": "quality-zero-platform",
                "open_issues": 0,
                "timestamp_utc": "2026-03-15T00:00:00+00:00",
                "findings": [],
            }
        )
        self.assertIn("- None", markdown)

        script_path = Path("scripts/quality/check_codacy_zero.py").resolve()
        root_text = str(Path.cwd().resolve())
        trimmed_sys_path = [item for item in sys.path if item != root_text]
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True), patch.object(
            sys,
            "argv",
            [str(script_path), "--owner", "Prekzursil", "--repo", "quality-zero-platform"],
        ), patch.object(sys, "path", trimmed_sys_path[:]):
            cwd = Path(tmp)
            previous = Path.cwd()
            os.chdir(cwd)
            try:
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(script_path), run_name="__main__")
            finally:
                os.chdir(previous)
        self.assertEqual(result.exception.code, 1)

    def test_repository_analysis_url_and_resolve_status_helpers_cover_public_paths(self) -> None:
        self.assertEqual(
            build_repository_analysis_url("gh", "Prekzursil", "quality-zero-platform"),
            "https://app.codacy.com/api/v3/analysis/organizations/gh/Prekzursil/repositories/quality-zero-platform",
        )
        args = Namespace(
            provider="gh",
            owner="Prekzursil",
            repo="quality-zero-platform",
            pull_request="",
            token="explicit-token",
            policy_mode="audit",
            out_json="codacy-zero/codacy.json",
            out_md="codacy-zero/codacy.md",
        )
        with patch.object(
            check_codacy_zero,
            "_query_codacy_open_issues",
            return_value=(3, ["Codacy reports 3 open issues (expected 0)."], None),
        ):
            result = _resolve_codacy_status(args)

        self.assertEqual(result.status, "pass")
        self.assertEqual(result.findings, ["Codacy reports 3 open issues (expected 0)."])
        self.assertEqual(result.open_issues, 3)
        self.assertEqual(result.pull_request, "")
        self.assertEqual(_build_payload(args, result)["open_issues"], 3)

