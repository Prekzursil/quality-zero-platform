"""Codacy main-entry and not-found coverage tests."""

from __future__ import absolute_import

import unittest
from argparse import Namespace
from unittest.mock import patch

import scripts.quality.check_codacy_zero as check_codacy_zero


def _marker(*parts: str) -> str:
    """Build one non-secret marker for tests."""
    return "-".join(parts)


class CodacyZeroAnalysisMainTests(unittest.TestCase):
    """Codacy main-entry and not-found tests."""

    def _build_args(
        self,
        *,
        pull_request: str = "",
        token: str | None = None,
        policy_mode: str = "zero",
    ) -> Namespace:
        """Build one argument namespace for main-entry tests."""
        return Namespace(
            provider="gh",
            owner="Prekzursil",
            repo="quality-zero-platform",
            pull_request=pull_request,
            token=_marker("explicit", "value") if token is None else token,
            policy_mode=policy_mode,
            out_json="codacy-zero/codacy.json",
            out_md="codacy-zero/codacy.md",
        )

    def test_not_found_findings_helpers(self) -> None:
        """Cover not-found findings with and without the last exception."""
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

        open_issues, findings, exc = check_codacy_zero._not_found_findings(["gh"], None)
        self.assertIsNone(open_issues)
        self.assertEqual(
            findings, ["Codacy API endpoint was not found for providers: gh."]
        )
        self.assertIsNone(exc)

    def test_main_status_requires_token(self) -> None:
        """Cover the missing-token main path."""
        args = self._build_args(token=str())
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(check_codacy_zero, "_parse_args", return_value=args),
            patch.object(check_codacy_zero, "write_report", return_value=0) as write_mock,
        ):
            self.assertEqual(check_codacy_zero.main(), 1)
        self.assertEqual(
            write_mock.call_args.args[0]["findings"],
            ["CODACY_API_TOKEN is missing."],
        )

    def test_main_status_passes_with_zero_issues(self) -> None:
        """Cover the successful main path."""
        args = self._build_args(pull_request="5")
        with (
            patch.object(check_codacy_zero, "_parse_args", return_value=args),
            patch.object(
                check_codacy_zero,
                "_query_codacy_open_issues",
                return_value=(0, [], None),
            ),
            patch.object(check_codacy_zero, "write_report", return_value=0) as write_mock,
        ):
            self.assertEqual(check_codacy_zero.main(), 0)
        self.assertEqual(write_mock.call_args.args[0]["status"], "pass")

    def test_main_status_returns_write_report_failure(self) -> None:
        """Cover write-report failures from the main path."""
        args = self._build_args(pull_request="5")
        with (
            patch.object(check_codacy_zero, "_parse_args", return_value=args),
            patch.object(
                check_codacy_zero,
                "_query_codacy_open_issues",
                return_value=(0, [], None),
            ),
            patch.object(check_codacy_zero, "write_report", return_value=7),
        ):
            self.assertEqual(check_codacy_zero.main(), 7)

    def test_main_status_audit_mode_keeps_success(self) -> None:
        """Cover audit-mode success when findings remain."""
        args = self._build_args(pull_request="5", policy_mode="audit")
        with (
            patch.object(check_codacy_zero, "_parse_args", return_value=args),
            patch.object(
                check_codacy_zero,
                "_query_codacy_open_issues",
                return_value=(5, ["Codacy reports 5 open issues (expected 0)."], None),
            ),
            patch.object(check_codacy_zero, "write_report", return_value=0),
        ):
            self.assertEqual(check_codacy_zero.main(), 0)
