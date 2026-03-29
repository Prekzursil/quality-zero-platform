from __future__ import absolute_import

import os
import runpy
import sys
import tempfile
import unittest
from argparse import Namespace
from email.message import Message
from pathlib import Path
from typing import List, Tuple
from urllib.error import HTTPError
from unittest.mock import patch

import scripts.quality.check_codacy_zero as check_codacy_zero
from scripts.quality.check_codacy_zero import (
    CodacyQuery,
    CodacyRetryConfig,
    CodacyStatusResult,
    _build_payload,
    _build_retry_config,
    _query_codacy_candidate,
    _query_codacy_open_issues,
    _query_codacy_provider,
    _request_mode,
    _write_codacy_report,
    build_issues_url,
    build_pull_request_analysis_url,
    build_repository_analysis_url,
    extract_total_open,
    load_codacy_findings_with_retry,
)


class CodacyZeroTests(unittest.TestCase):
    @staticmethod
    def _base_query(*, provider: str = "gh", pull_request: str = "") -> CodacyQuery:
        return CodacyQuery(provider, "Prekzursil", "quality-zero-platform", pull_request=pull_request)

    @staticmethod
    def _retry_config(
        provider_candidates: Tuple[str, ...],
        *,
        attempts: int = 1,
        pending_fn=check_codacy_zero._analysis_pending_message,
        sleep_seconds: float = 0.0,
    ) -> CodacyRetryConfig:
        return CodacyRetryConfig(
            provider_candidates=provider_candidates,
            attempts=attempts,
            pending_fn=pending_fn,
            sleep_seconds=sleep_seconds,
        )

    def test_build_urls_and_request_mode(self) -> None:
        self.assertEqual(_request_mode(self._base_query()), ("POST", {}))
        self.assertEqual(_request_mode(self._base_query(pull_request="5")), ("GET", None))
        self.assertEqual(
            build_issues_url("gh", "Prekzursil", "quality-zero-platform", pull_request=""),
            "https://api.codacy.com/api/v3/analysis/organizations/gh/Prekzursil/repositories/quality-zero-platform/issues/search?limit=1",
        )
        self.assertEqual(
            build_issues_url("gh", "Prekzursil", "quality-zero-platform", pull_request="5"),
            "https://app.codacy.com/api/v3/analysis/organizations/gh/Prekzursil/repositories/quality-zero-platform/pull-requests/5/issues?status=new&limit=1",
        )
        self.assertEqual(
            build_repository_analysis_url("gh", "Prekzursil", "quality-zero-platform"),
            "https://app.codacy.com/api/v3/analysis/organizations/gh/Prekzursil/repositories/quality-zero-platform",
        )
        self.assertEqual(
            build_pull_request_analysis_url("gh", "Prekzursil", "quality-zero-platform", "5"),
            "https://app.codacy.com/api/v3/analysis/organizations/gh/Prekzursil/repositories/quality-zero-platform/pull-requests/5",
        )

    def test_extract_total_open_nested(self) -> None:
        self.assertEqual(extract_total_open({"issuesCount": 4}), 4)
        self.assertEqual(extract_total_open({"paging": {"total": 3}}), 3)
        self.assertEqual(extract_total_open([{"details": {"open_issues": 2}}]), 2)
        self.assertIsNone(extract_total_open({"items": [{"details": "no-count"}]}))

    def test_request_json_rejects_non_dict_payloads(self) -> None:
        with patch("scripts.quality.check_codacy_zero.load_json_https", return_value=(["invalid"], {})):
            with self.assertRaisesRegex(RuntimeError, "Unexpected Codacy API response payload"):
                check_codacy_zero._request_json("https://api.codacy.com/test", "token")

        with patch("scripts.quality.check_codacy_zero.load_json_https", return_value=({"total": 0}, {})):
            self.assertEqual(check_codacy_zero._request_json("https://api.codacy.com/test", "token"), {"total": 0})

    def test_public_repository_issue_query_paths(self) -> None:
        with patch("scripts.quality.check_codacy_zero.load_json_https", return_value=({"issuesCount": 0}, {})):
            self.assertEqual(
                check_codacy_zero._query_codacy_public_repository_issues("gh", "Prekzursil", "quality-zero-platform"),
                (0, []),
            )
        with patch("scripts.quality.check_codacy_zero.load_json_https", return_value=("bad", {})):
            with self.assertRaisesRegex(RuntimeError, "Unexpected Codacy public repository payload"):
                check_codacy_zero._query_codacy_public_repository_issues("gh", "Prekzursil", "quality-zero-platform")
        with patch("scripts.quality.check_codacy_zero.load_json_https", return_value=({"items": []}, {})):
            self.assertEqual(
                check_codacy_zero._query_codacy_public_repository_issues("gh", "Prekzursil", "quality-zero-platform"),
                (None, ["Codacy response did not include a parseable total issue count."]),
            )
        with patch("scripts.quality.check_codacy_zero.load_json_https", return_value=({"issuesCount": 4}, {})):
            self.assertEqual(
                check_codacy_zero._query_codacy_public_repository_issues("gh", "Prekzursil", "quality-zero-platform"),
                (4, ["Codacy reports 4 open issues (expected 0)."]),
            )

    def test_provider_query_paths(self) -> None:
        with patch("scripts.quality.check_codacy_zero._request_json", return_value={"items": []}):
            self.assertEqual(
                _query_codacy_provider(self._base_query(), "token"),
                (None, ["Codacy response did not include a parseable total issue count."]),
            )
        with patch("scripts.quality.check_codacy_zero._request_json", return_value={"total": 2}):
            self.assertEqual(
                _query_codacy_provider(self._base_query(), "token"),
                (2, ["Codacy reports 2 open issues (expected 0)."]),
            )

    def test_retry_config_builder_and_pending_helpers_cover_analysis_paths(self) -> None:
        unscoped = _build_retry_config(self._base_query(), ["gh"])
        self.assertEqual(unscoped.attempts, 1)
        self.assertEqual(unscoped.provider_candidates, ("gh",))
        scoped = _build_retry_config(
            CodacyQuery("gh", "Prekzursil", "quality-zero-platform", pull_request="5", sha="targetsha"),
            ["gh", "github"],
            sleep_seconds=-1.0,
        )
        self.assertEqual(scoped.attempts, check_codacy_zero.SCOPED_ANALYSIS_RETRY_ATTEMPTS)
        self.assertEqual(scoped.sleep_seconds, 0.0)

        with patch("scripts.quality.check_codacy_zero.load_json_https", return_value=("bad", {})):
            with self.assertRaisesRegex(RuntimeError, "Unexpected Codacy analysis status payload"):
                check_codacy_zero._request_analysis_status("https://app.codacy.com/api/v3/test", "token")
        with patch("scripts.quality.check_codacy_zero.load_json_https", return_value=({"data": {}}, {})):
            self.assertEqual(
                check_codacy_zero._request_analysis_status("https://app.codacy.com/api/v3/test", "token"),
                {"data": {}},
            )

        pr_query = CodacyQuery("gh", "Prekzursil", "quality-zero-platform", pull_request="5", sha="targetsha")
        with patch.object(check_codacy_zero, "_request_analysis_status", return_value={"isAnalysing": True}):
            self.assertEqual(
                check_codacy_zero._analysis_pending_message(pr_query, "token"),
                "Codacy is still analysing pull request 5.",
            )
        with patch.object(check_codacy_zero, "_request_analysis_status", return_value={"pullRequest": {}}):
            self.assertEqual(
                check_codacy_zero._analysis_pending_message(pr_query, "token"),
                "Codacy analysis for pull request 5 is not available yet.",
            )
        with patch.object(
            check_codacy_zero,
            "_request_analysis_status",
            return_value={"pullRequest": {"headCommitSha": "oldsha"}},
        ):
            self.assertEqual(
                check_codacy_zero._analysis_pending_message(pr_query, "token"),
                "Codacy analysis for pull request 5 is still on oldsha (waiting for targetsha).",
            )
        with patch.object(
            check_codacy_zero,
            "_request_analysis_status",
            return_value={"pullRequest": {"headCommitSha": "targetsha"}},
        ):
            self.assertIsNone(check_codacy_zero._analysis_pending_message(pr_query, "token"))

        repo_query = CodacyQuery("gh", "Prekzursil", "quality-zero-platform", sha="targetsha")
        with patch.object(check_codacy_zero, "_request_analysis_status", return_value={"data": {}}):
            self.assertEqual(
                check_codacy_zero._analysis_pending_message(repo_query, "token"),
                "Codacy analysis for repository is not available yet.",
            )
        with patch.object(
            check_codacy_zero,
            "_request_analysis_status",
            return_value={"data": {"lastAnalysedCommit": {"sha": "oldsha"}}},
        ):
            self.assertEqual(
                check_codacy_zero._analysis_pending_message(repo_query, "token"),
                "Codacy analysis for repository is still on oldsha (waiting for targetsha).",
            )
        with patch.object(
            check_codacy_zero,
            "_request_analysis_status",
            return_value={"data": {"lastAnalysedCommit": {"sha": "targetsha"}}},
        ):
            self.assertEqual(
                check_codacy_zero._analysis_pending_message(repo_query, "token"),
                "Codacy repository analysis has not finished yet.",
            )
        with patch.object(
            check_codacy_zero,
            "_request_analysis_status",
            return_value={"data": {"lastAnalysedCommit": {"sha": "targetsha", "endedAnalysis": "done"}}},
        ):
            self.assertIsNone(check_codacy_zero._analysis_pending_message(repo_query, "token"))

    def test_open_issue_query_paths(self) -> None:
        responses = [Exception("sentinel"), {"total": 0}]

        def fake_request(url: str, token: str, *, method: str = "GET", data=None):
            current = responses.pop(0)
            if isinstance(current, Exception):
                from urllib.error import HTTPError
                raise HTTPError(url, 404, "Not Found", hdrs=Message(), fp=None)
            return current

        with patch("scripts.quality.check_codacy_zero._request_json", side_effect=fake_request):
            self.assertEqual(_query_codacy_open_issues(self._base_query(), "token", ["custom", "gh"]), (0, [], None))

        captured: List[Tuple[str, str, object | None]] = []

        def capture_request(url: str, token: str, *, method: str = "GET", data=None):
            captured.append((url, method, data))
            return {"total": 0}

        with patch("scripts.quality.check_codacy_zero._request_json", side_effect=capture_request):
            self.assertEqual(
                _query_codacy_open_issues(self._base_query(pull_request="5"), "token", ["gh"]),
                (0, [], None),
            )
        self.assertEqual(
            captured,
            [
                (
                    "https://app.codacy.com/api/v3/analysis/organizations/gh/Prekzursil/"
                    "repositories/quality-zero-platform/pull-requests/5/issues?status=new&limit=1",
                    "GET",
                    None,
                )
            ],
        )

    def test_open_issue_http_error_and_not_found_paths(self) -> None:
        from urllib.error import HTTPError

        error = HTTPError("https://api.codacy.com", 401, "Unauthorized", hdrs=Message(), fp=None)
        with patch("scripts.quality.check_codacy_zero._query_codacy_provider", side_effect=error), patch(
            "scripts.quality.check_codacy_zero._query_codacy_public_repository_issues",
            return_value=(0, []),
        ) as fallback_mock:
            self.assertEqual(
                _query_codacy_open_issues(self._base_query(), "token", ["gh"]),
                (0, [], None),
            )
        fallback_mock.assert_called_once_with("gh", "Prekzursil", "quality-zero-platform")

        with patch(
            "scripts.quality.check_codacy_zero._query_codacy_provider",
            side_effect=HTTPError("u", 500, "Boom", hdrs=Message(), fp=None),
        ):
            open_issues, findings, exc = _query_codacy_open_issues(self._base_query(), "token", ["gh"])
        self.assertIsNone(open_issues)
        self.assertEqual(findings, ["Codacy API request failed: HTTP 500"])
        self.assertIsNotNone(exc)

        with patch(
            "scripts.quality.check_codacy_zero._query_codacy_provider",
            side_effect=RuntimeError("network broke"),
        ):
            open_issues, findings, exc = _query_codacy_open_issues(self._base_query(), "token", ["gh"])
        self.assertIsNone(open_issues)
        self.assertEqual(findings, ["Codacy API request failed: network broke"])
        self.assertIsInstance(exc, RuntimeError)

        def fake_provider(*_args, **_kwargs):
            raise HTTPError("https://api.codacy.com", 404, "Not Found", hdrs=Message(), fp=None)

        with patch(
            "scripts.quality.check_codacy_zero._query_codacy_provider",
            side_effect=fake_provider,
        ):
            open_issues, findings, exc = _query_codacy_open_issues(
                self._base_query(),
                "token",
                ["custom", "github"],
            )
        self.assertIsNone(open_issues)
        self.assertIn("Codacy API endpoint was not found", findings[0])
        self.assertIsNotNone(exc)

    def test_fallback_and_http_error_helpers_cover_remaining_branches(self) -> None:
        from urllib.error import HTTPError

        self.assertIsNone(check_codacy_zero._fallback_public_issues(self._base_query(pull_request="5")))

        error = HTTPError("https://api.codacy.com", 401, "Unauthorized", hdrs=Message(), fp=None)
        with patch("scripts.quality.check_codacy_zero._fallback_public_issues", return_value=None):
            self.assertEqual(
                check_codacy_zero._handle_codacy_http_error(error, self._base_query()),
                (None, ["Codacy API request failed: HTTP 401"], error, True),
            )

        with patch(
            "scripts.quality.check_codacy_zero._fallback_public_issues",
            return_value=(None, [], RuntimeError("fallback broke")),
        ):
            open_issues, findings, exc, should_return = check_codacy_zero._handle_codacy_http_error(
                error,
                self._base_query(),
            )
        self.assertIsNone(open_issues)
        self.assertEqual(findings, [])
        self.assertIsInstance(exc, RuntimeError)
        self.assertFalse(should_return)

    def test_query_candidate_and_helpers(self) -> None:
        query = self._base_query()
        with patch(
            "scripts.quality.check_codacy_zero._query_codacy_provider",
            return_value=(0, []),
        ):
            self.assertEqual(
                _query_codacy_candidate(query, "token"),
                (0, [], None, True),
            )
        with patch(
            "scripts.quality.check_codacy_zero._query_codacy_provider",
            side_effect=RuntimeError("provider broke"),
        ):
            open_issues, findings, exc, should_return = _query_codacy_candidate(query, "token")
        self.assertIsNone(open_issues)
        self.assertEqual(findings, ["Codacy API request failed: provider broke"])
        self.assertIsInstance(exc, RuntimeError)
        self.assertTrue(should_return)
        open_issues, findings, exc = check_codacy_zero._not_found_findings(
            ["gh"],
            RuntimeError("boom"),
        )
        self.assertIsNone(open_issues)
        self.assertEqual(
            findings,
            [
                "Codacy API endpoint was not found for providers: gh.",
                "Last Codacy API error: boom",
            ],
        )
        self.assertIsInstance(exc, RuntimeError)

    def test_not_found_findings_without_exception(self) -> None:
        open_issues, findings, exc = check_codacy_zero._not_found_findings(["gh"], None)
        self.assertIsNone(open_issues)
        self.assertEqual(findings, ["Codacy API endpoint was not found for providers: gh."])
        self.assertIsNone(exc)

    def test_main_status_paths(self) -> None:
        empty_value = str()
        args = Namespace(
            provider="gh",
            owner="Prekzursil",
            repo="quality-zero-platform",
            pull_request=empty_value,
            token=empty_value,
            out_json="codacy-zero/codacy.json",
            out_md="codacy-zero/codacy.md",
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(check_codacy_zero, "_parse_args", return_value=args),
            patch.object(check_codacy_zero, "write_report", return_value=0) as write_report_mock,
        ):
            self.assertEqual(check_codacy_zero.main(), 1)
        self.assertEqual(write_report_mock.call_args.args[0]["findings"], ["CODACY_API_TOKEN is missing."])

        success_args = Namespace(
            **{**args.__dict__, "token": "explicit-token", "pull_request": "5"}
        )
        with (
            patch.object(check_codacy_zero, "_parse_args", return_value=success_args),
            patch.object(check_codacy_zero, "_query_codacy_open_issues", return_value=(0, [], None)),
            patch.object(check_codacy_zero, "write_report", return_value=0) as write_report_mock,
        ):
            self.assertEqual(check_codacy_zero.main(), 0)
        self.assertEqual(write_report_mock.call_args.args[0]["status"], "pass")

        with patch.object(check_codacy_zero, "_parse_args", return_value=success_args), patch.object(
            check_codacy_zero, "_query_codacy_open_issues", return_value=(0, [], None)
        ), patch.object(check_codacy_zero, "write_report", return_value=7):
            self.assertEqual(check_codacy_zero.main(), 7)

        audit_args = Namespace(**{**success_args.__dict__, "policy_mode": "audit"})
        with (
            patch.object(check_codacy_zero, "_parse_args", return_value=audit_args),
            patch.object(
                check_codacy_zero,
                "_query_codacy_open_issues",
                return_value=(5, ["Codacy reports 5 open issues (expected 0)."], None),
            ),
            patch.object(check_codacy_zero, "write_report", return_value=0),
        ):
            self.assertEqual(check_codacy_zero.main(), 0)

    def test_load_codacy_findings_with_retry_retries_pull_request_404s(self) -> None:
        calls: List[int] = []

        def fake_query(*_args, **_kwargs):
            calls.append(len(calls))
            if len(calls) == 1:
                return None, [], HTTPError("https://api.codacy.com", 404, "Not Found", hdrs=Message(), fp=None)
            return 0, [], None

        with patch.object(check_codacy_zero, "SCOPED_ANALYSIS_RETRY_ATTEMPTS", 2), patch.object(
            check_codacy_zero.time, "sleep", return_value=None
        ), patch.object(check_codacy_zero, "_query_codacy_open_issues", side_effect=fake_query):
            open_issues, findings = load_codacy_findings_with_retry(
                self._base_query(pull_request="49"),
                "token",
                self._retry_config(("gh",), attempts=2),
            )

        self.assertEqual((open_issues, findings), (0, []))
        self.assertEqual(len(calls), 2)

    def test_load_codacy_findings_with_retry_returns_last_findings_after_retry_budget(self) -> None:
        not_found = HTTPError("https://api.codacy.com", 404, "Not Found", hdrs=Message(), fp=None)
        with patch.object(check_codacy_zero, "SCOPED_ANALYSIS_RETRY_ATTEMPTS", 2), patch.object(
            check_codacy_zero.time, "sleep", return_value=None
        ), patch.object(
            check_codacy_zero,
            "_query_codacy_open_issues",
            return_value=(None, ["Codacy API endpoint was not found for providers: gh, github."], not_found),
        ) as query_mock:
            open_issues, findings = load_codacy_findings_with_retry(
                self._base_query(pull_request="49"),
                "token",
                self._retry_config(("gh", "github"), attempts=2),
            )

        self.assertIsNone(open_issues)
        self.assertEqual(findings, ["Codacy API endpoint was not found for providers: gh, github."])
        self.assertEqual(query_mock.call_count, 2)

    def test_load_codacy_findings_with_retry_waits_for_target_sha(self) -> None:
        attempts: List[int] = []
        base_query = CodacyQuery("gh", "Prekzursil", "quality-zero-platform", sha="targetsha")
        pending_responses = [
            "Codacy repository analysis is still on oldsha (waiting for targetsha).",
            None,
        ]

        def fake_query(*_args, **_kwargs):
            attempts.append(len(attempts) + 1)
            return 0, [], None

        with patch.object(check_codacy_zero, "_query_codacy_open_issues", side_effect=fake_query):
            open_issues, findings = load_codacy_findings_with_retry(
                base_query,
                "token",
                self._retry_config(
                    ("gh",),
                    attempts=2,
                    pending_fn=lambda _query, _token: pending_responses.pop(0),
                ),
            )

        self.assertEqual((open_issues, findings), (0, []))
        self.assertEqual(attempts, [1, 2])

    def test_load_codacy_findings_with_retry_reports_pending_analysis_after_budget(self) -> None:
        base_query = CodacyQuery("gh", "Prekzursil", "quality-zero-platform", sha="targetsha")

        with patch.object(
            check_codacy_zero,
            "_query_codacy_open_issues",
            return_value=(0, [], None),
        ):
            open_issues, findings = load_codacy_findings_with_retry(
                base_query,
                "token",
                self._retry_config(
                    ("gh",),
                    pending_fn=lambda _query, _token: "Codacy repository analysis is not available yet.",
                ),
            )

        self.assertEqual(open_issues, 0)
        self.assertEqual(findings, ["Codacy repository analysis is not available yet."])

    def test_load_codacy_findings_with_retry_does_not_retry_without_pull_request(self) -> None:
        with patch.object(
            check_codacy_zero,
            "_query_codacy_open_issues",
            return_value=(0, [], None),
        ) as query_mock:
            open_issues, findings = load_codacy_findings_with_retry(
                self._base_query(),
                "token",
                self._retry_config(("gh",)),
            )

        self.assertEqual((open_issues, findings), (0, []))
        query_mock.assert_called_once()

    def test_load_codacy_findings_with_retry_returns_immediately_for_pull_request_success(self) -> None:
        with patch.object(
            check_codacy_zero,
            "_query_codacy_open_issues",
            return_value=(0, [], None),
        ) as query_mock, patch.object(check_codacy_zero.time, "sleep", return_value=None) as sleep_mock:
            open_issues, findings = load_codacy_findings_with_retry(
                self._base_query(pull_request="49"),
                "token",
                self._retry_config(("gh",), attempts=2),
            )

        self.assertEqual((open_issues, findings), (0, []))
        query_mock.assert_called_once()
        sleep_mock.assert_not_called()

    def test_load_codacy_findings_with_retry_reports_pending_status_failures(self) -> None:
        with patch.object(
            check_codacy_zero,
            "_query_codacy_open_issues",
            return_value=(0, [], None),
        ):
            open_issues, findings = load_codacy_findings_with_retry(
                CodacyQuery("gh", "Prekzursil", "quality-zero-platform", sha="targetsha"),
                "token",
                self._retry_config(
                    ("gh",),
                    pending_fn=lambda _query, _token: (_ for _ in ()).throw(RuntimeError("status broke")),
                ),
            )

        self.assertEqual(open_issues, 0)
        self.assertEqual(findings, ["Codacy analysis status request failed: status broke"])

    def test_payload_and_report_helpers(self) -> None:
        payload = _build_payload(
            Namespace(provider="gh", owner="Prekzursil", repo="quality-zero-platform"),
            CodacyStatusResult(status="pass", findings=["done"], open_issues=0, pull_request=""),
        )
        self.assertIn("- done", check_codacy_zero._render_md(payload))
        with patch.object(check_codacy_zero, "write_report", return_value=0) as write_report_mock:
            self.assertEqual(
                _write_codacy_report(
                    Namespace(out_json="codacy-zero/codacy.json", out_md="codacy-zero/codacy.md"),
                    payload,
                ),
                0,
            )
        self.assertEqual(write_report_mock.call_args.kwargs["render_md"], check_codacy_zero._render_md)

    def test_parse_args_and_script_entrypoint(self) -> None:
        with patch.object(sys, "argv", ["check_codacy_zero.py", "--owner", "Prekzursil", "--repo", "quality-zero-platform"]):
            args = check_codacy_zero._parse_args()
        self.assertEqual(args.provider, "gh")
        self.assertEqual(args.out_json, "codacy-zero/codacy.json")

        script_path = Path("scripts/quality/check_codacy_zero.py").resolve()
        root_text = str(Path.cwd().resolve())
        trimmed_sys_path = [item for item in sys.path if item != root_text]
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict("os.environ", {}, clear=True),
            patch.object(
                sys,
                "argv",
                [str(script_path), "--owner", "Prekzursil", "--repo", "quality-zero-platform"],
            ),
            patch.object(sys, "path", trimmed_sys_path[:]),
        ):
            cwd = Path(tmp)
            previous = Path.cwd()
            os.chdir(cwd)
            try:
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(script_path), run_name="__main__")
            finally:
                os.chdir(previous)
        self.assertEqual(result.exception.code, 1)
