"""Test quality common."""

from __future__ import absolute_import

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List
from unittest.mock import patch

from scripts.quality import profile_coverage_normalization
from scripts.quality.common import (
    ReportSpec,
    _deep_merge,
    _resolve_report_spec,
    dedupe_strings,
    finalize_vendors,
    infer_coverage_inputs,
    merge_required_contexts,
    normalize_codex_environment,
    normalize_coverage,
    normalize_coverage_assert_mode,
    normalize_coverage_inputs,
    normalize_deps,
    normalize_issue_policy,
    normalize_coverage_setup,
    normalize_java_setup,
    normalize_required_contexts,
    safe_output_path,
    utc_timestamp,
    write_report,
)


@contextlib.contextmanager
def _temporary_cwd(target: Path):
    """Handle temporary cwd."""
    previous = Path.cwd()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(previous)


class QualityCommonTests(unittest.TestCase):
    """Quality Common Tests."""

    @staticmethod
    def _normalized_explicit_coverage() -> dict:
        """Handle normalized explicit coverage."""
        return normalize_coverage(
            {
                "runner": " ",
                "shell": "",
                "command": "  qlty check  ",
                "inputs": [
                    {"format": "xml", "name": "coverage", "path": "coverage.xml"}
                ],
                "require_sources": [" source-a ", "source-a", "source-b"],
                "min_percent": "98.5",
                "assert_mode": {"default": "", "python": " warn "},
                "evidence_note": "  note  ",
                "setup": {
                    "python": " 3.11 ",
                    "node": " 20 ",
                    "go": " 1.22 ",
                    "dotnet": " 8 ",
                    "rust": "yes",
                    "system_packages": [" git ", "curl", "git"],
                    "java": {"distribution": " temurin ", "version": " 21 "},
                },
            }
        )

    @staticmethod
    def _inferred_coverage() -> dict:
        """Handle inferred coverage."""
        return normalize_coverage(
            {
                "command": (
                    "python -m pytest --cov=scripts --cov=scripts.quality.assert_coverage_100 "
                    "--cov=scripts.quality.check_sentry_zero && "
                    "gcovr --filter '.*/src/.*' && "
                    "npm --prefix airline-gui test -- --coverage --watch=false"
                ),
                "inputs": [
                    {"format": "xml", "name": "coverage", "path": "coverage.xml"},
                    {
                        "format": "lcov",
                        "name": "frontend",
                        "path": "airline-gui/coverage/lcov.info",
                    },
                ],
            }
        )

    def test_resolve_report_spec_supports_report_spec_and_validates_legacy_kwargs(
        self,
    ) -> None:
        """Cover resolve report spec supports report spec and validates legacy kwargs."""
        report_spec = ReportSpec(
            out_json="reports/out.json",
            out_md="reports/out.md",
            default_json="fallback.json",
            default_md="fallback.md",
            render_md=lambda payload: f"# {payload['status']}\n",
        )

        self.assertIs(_resolve_report_spec(report_spec), report_spec)

        with self.assertRaisesRegex(
            TypeError,
            "write_report expects a ReportSpec or legacy keyword arguments",
        ):
            _resolve_report_spec(report_spec, extra=True)

        with self.assertRaisesRegex(
            TypeError,
            "write_report expects a ReportSpec or legacy keyword arguments",
        ):
            _resolve_report_spec("legacy-positional-arg")

        with self.assertRaisesRegex(
            TypeError, "Missing required report parameter: render_md"
        ):
            _resolve_report_spec(
                out_json="reports/out.json",
                out_md="reports/out.md",
                default_json="fallback.json",
                default_md="fallback.md",
            )

        with self.assertRaisesRegex(
            TypeError, "Unexpected write_report parameters: extra"
        ):
            _resolve_report_spec(
                out_json="reports/out.json",
                out_md="reports/out.md",
                default_json="fallback.json",
                default_md="fallback.md",
                render_md=lambda _payload: "markdown\n",
                extra=True,
            )

    def test_utc_timestamp_is_timezone_aware(self) -> None:
        """Cover utc timestamp is timezone aware."""
        timestamp = utc_timestamp()
        parsed = datetime.fromisoformat(timestamp)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_dedupe_strings_trims_skips_empty_and_preserves_first_seen_order(
        self,
    ) -> None:
        """Cover dedupe strings trims skips empty and preserves first seen order."""
        values: List[Any] = ["  alpha ", "", "beta", "alpha", None, " beta ", "gamma"]
        self.assertEqual(
            dedupe_strings(values),
            ["alpha", "beta", "gamma"],
        )

    def test_safe_output_path_uses_fallback_and_rejects_workspace_escape(self) -> None:
        """Cover safe output path uses fallback and rejects workspace escape."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with _temporary_cwd(root):
                fallback_path = safe_output_path("", "reports/out.json")
                self.assertEqual(fallback_path.name, "out.json")
                self.assertTrue(fallback_path.as_posix().endswith("/reports/out.json"))

                nested_path = safe_output_path("nested/report.md", "ignored.txt")
                self.assertEqual(nested_path.name, "report.md")
                self.assertTrue(nested_path.as_posix().endswith("/nested/report.md"))
                absolute_candidate = str((root / "absolute-outside.json").resolve())
                self.assertEqual(
                    safe_output_path(absolute_candidate, "reports/out.json"),
                    Path(absolute_candidate),
                )
                with self.assertRaisesRegex(ValueError, "escapes workspace root"):
                    safe_output_path("../outside.json", "reports/out.json")

    def test_write_report_writes_both_formats_and_prints_markdown(self) -> None:
        """Cover write report writes both formats and prints markdown."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {"status": "pass", "items": ["one", "two"]}
            stdout = io.StringIO()

            with _temporary_cwd(root), contextlib.redirect_stdout(stdout):
                result = write_report(
                    payload,
                    out_json="reports/result.json",
                    out_md="reports/result.md",
                    default_json="fallback.json",
                    default_md="fallback.md",
                    render_md=lambda current: f"# Report\n\n- Status: {current['status']}\n",
                )

            self.assertEqual(result, 0)
            json_payload = json.loads(
                (root / "reports" / "result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(json_payload, payload)
            self.assertEqual(
                (root / "reports" / "result.md").read_text(encoding="utf-8"),
                "# Report\n\n- Status: pass\n",
            )
            self.assertEqual(stdout.getvalue(), "# Report\n\n- Status: pass\n")

    def test_write_report_returns_non_zero_when_output_path_is_invalid(self) -> None:
        """Cover write report returns non zero when output path is invalid."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stderr = io.StringIO()
            with _temporary_cwd(root), contextlib.redirect_stderr(stderr):
                result = write_report(
                    {"status": "fail"},
                    out_json="../escape.json",
                    out_md="reports/result.md",
                    default_json="fallback.json",
                    default_md="fallback.md",
                    render_md=lambda _payload: "ignored\n",
                )

            self.assertEqual(result, 1)
            self.assertIn("escapes workspace root", stderr.getvalue())

    def test_normalize_context_helpers_cover_defaults_and_merges(self) -> None:
        """Cover normalize context helpers cover defaults and merges."""
        self.assertEqual(
            normalize_required_contexts(None),
            {
                "always": [],
                "pull_request_only": [],
                "required_now": [],
                "target": [],
            },
        )
        self.assertEqual(
            normalize_required_contexts(
                {
                    "always": ["  Coverage 100 Gate  ", "", "qlty check"],
                    "pull_request_only": ["qlty check", "Semgrep Zero", "  "],
                    "required_now": [],
                    "target": [],
                }
            ),
            {
                "always": ["Coverage 100 Gate", "qlty check"],
                "pull_request_only": ["Semgrep Zero"],
                "required_now": ["Coverage 100 Gate", "qlty check", "Semgrep Zero"],
                "target": ["Coverage 100 Gate", "qlty check", "Semgrep Zero"],
            },
        )
        self.assertEqual(
            merge_required_contexts(
                {
                    "always": ["Coverage 100 Gate"],
                    "required_now": ["Coverage 100 Gate"],
                    "target": ["Coverage 100 Gate"],
                },
                {
                    "always": ["qlty check"],
                    "pull_request_only": ["qlty check", "DeepScan Zero"],
                    "required_now": ["qlty check", "DeepScan Zero"],
                    "target": ["qlty check", "DeepScan Zero"],
                },
            ),
            {
                "always": ["Coverage 100 Gate", "qlty check"],
                "pull_request_only": ["DeepScan Zero"],
                "required_now": ["Coverage 100 Gate", "qlty check", "DeepScan Zero"],
                "target": ["Coverage 100 Gate", "qlty check", "DeepScan Zero"],
            },
        )

    def test_normalize_coverage_helpers_cover_filters_and_fallbacks(self) -> None:
        """Cover normalize coverage helpers cover filters and fallbacks."""
        self.assertEqual(normalize_coverage_inputs("not-a-list"), [])
        self.assertEqual(
            normalize_coverage_inputs(
                [
                    {"format": " XML ", "name": "coverage", "path": "coverage.xml"},
                    {"format": "lcov", "name": "lcov", "path": "lcov.info"},
                    {"format": "json", "name": "skip", "path": "skip.json"},
                    {"format": "xml", "name": "", "path": "missing-name.xml"},
                    "ignored-entry",
                ]
            ),
            [
                {"format": "xml", "name": "coverage", "path": "coverage.xml"},
                {"format": "lcov", "name": "lcov", "path": "lcov.info"},
            ],
        )
        self.assertEqual(
            infer_coverage_inputs(
                {
                    "inputs": [
                        {"format": "lcov", "name": "existing", "path": "cov.info"}
                    ],
                    "artifact_path": "coverage.xml",
                }
            ),
            [{"format": "lcov", "name": "existing", "path": "cov.info"}],
        )
        self.assertEqual(
            infer_coverage_inputs({"artifact_path": "coverage/coverage.xml"}),
            [{"format": "xml", "name": "default", "path": "coverage/coverage.xml"}],
        )
        self.assertEqual(
            infer_coverage_inputs({"artifact_path": "coverage/lcov.info"}),
            [{"format": "lcov", "name": "default", "path": "coverage/lcov.info"}],
        )


class EnsureWithinRootPR1Tests(unittest.TestCase):
    """Augmenting tests for scripts.quality.common._ensure_within_root (per QRv2 §B.2.3).

    These tests assume callers pre-resolve paths via Path.resolve(strict=False)
    before invoking the helper — this is the contract that
    scripts/quality/rollup_v2/path_safety.validate_finding_file enforces in PR 1.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name).resolve()
        (self.tmp_root / "a").mkdir()
        (self.tmp_root / "a" / "b.py").write_text("pass", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_well_formed_resolved_path_accepted(self):
        from scripts.quality.common import _ensure_within_root
        _ensure_within_root(
            (self.tmp_root / "a" / "b.py").resolve(strict=False),
            self.tmp_root,
        )

    def test_resolved_dotdot_escape_rejected(self):
        from scripts.quality.common import _ensure_within_root
        escape = (self.tmp_root / ".." / ".." / "etc" / "passwd").resolve(strict=False)
        with self.assertRaises(ValueError):
            _ensure_within_root(escape, self.tmp_root)

    def test_absolute_escape_rejected(self):
        from scripts.quality.common import _ensure_within_root
        with self.assertRaises(ValueError):
            _ensure_within_root(Path("/etc/passwd"), self.tmp_root)

    @unittest.skipIf(sys.platform == "win32", "POSIX symlink behavior required")
    def test_symlink_escape_rejected_when_resolved_by_caller(self):
        escape_link = self.tmp_root / "a" / "escape"
        escape_link.symlink_to(Path("/etc/passwd"))
        from scripts.quality.common import _ensure_within_root
        with self.assertRaises(ValueError):
            _ensure_within_root(escape_link.resolve(strict=False), self.tmp_root)

