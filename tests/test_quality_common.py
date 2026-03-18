from __future__ import absolute_import

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

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
    normalize_coverage_setup,
    normalize_java_setup,
    normalize_required_contexts,
    safe_output_path,
    utc_timestamp,
    write_report,
)


@contextlib.contextmanager
def _temporary_cwd(target: Path):
    previous = Path.cwd()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(previous)


class QualityCommonTests(unittest.TestCase):
    def test_resolve_report_spec_supports_report_spec_and_validates_legacy_kwargs(self) -> None:
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

        with self.assertRaisesRegex(TypeError, "Missing required report parameter: render_md"):
            _resolve_report_spec(
                out_json="reports/out.json",
                out_md="reports/out.md",
                default_json="fallback.json",
                default_md="fallback.md",
            )

        with self.assertRaisesRegex(TypeError, "Unexpected write_report parameters: extra"):
            _resolve_report_spec(
                out_json="reports/out.json",
                out_md="reports/out.md",
                default_json="fallback.json",
                default_md="fallback.md",
                render_md=lambda _payload: "markdown\n",
                extra=True,
            )

    def test_utc_timestamp_is_timezone_aware(self) -> None:
        timestamp = utc_timestamp()
        parsed = datetime.fromisoformat(timestamp)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_dedupe_strings_trims_skips_empty_and_preserves_first_seen_order(self) -> None:
        self.assertEqual(
            dedupe_strings(["  alpha ", "", "beta", "alpha", None, " beta ", "gamma"]),
            ["alpha", "beta", "gamma"],
        )

    def test_safe_output_path_uses_fallback_and_rejects_workspace_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with _temporary_cwd(root):
                fallback_path = safe_output_path("", "reports/out.json")
                self.assertEqual(fallback_path.name, "out.json")
                self.assertTrue(fallback_path.as_posix().endswith("/reports/out.json"))

                nested_path = safe_output_path("nested/report.md", "ignored.txt")
                self.assertEqual(nested_path.name, "report.md")
                self.assertTrue(nested_path.as_posix().endswith("/nested/report.md"))
                with self.assertRaisesRegex(ValueError, "escapes workspace root"):
                    safe_output_path("../outside.json", "reports/out.json")

    def test_write_report_writes_both_formats_and_prints_markdown(self) -> None:
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
            json_payload = json.loads((root / "reports" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(json_payload, payload)
            self.assertEqual(
                (root / "reports" / "result.md").read_text(encoding="utf-8"),
                "# Report\n\n- Status: pass\n",
            )
            self.assertEqual(stdout.getvalue(), "# Report\n\n- Status: pass\n")

    def test_write_report_returns_non_zero_when_output_path_is_invalid(self) -> None:
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
            infer_coverage_inputs({"inputs": [{"format": "lcov", "name": "existing", "path": "cov.info"}], "artifact_path": "coverage.xml"}),
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

    def test_normalize_setup_helpers_cover_string_inputs(self) -> None:
        self.assertEqual(normalize_java_setup("21"), {"distribution": "temurin", "version": "21"})
        self.assertEqual(normalize_java_setup(None), {"distribution": "", "version": ""})
        self.assertEqual(
            normalize_coverage_setup(
                {
                    "python": " 3.12 ",
                    "node": " 20 ",
                    "go": " 1.22 ",
                    "dotnet": " 8 ",
                    "rust": 1,
                    "system_packages": [" curl ", "git", "curl", "  "],
                    "java": "17",
                }
            ),
            {
                "python": "3.12",
                "node": "20",
                "go": "1.22",
                "dotnet": "8",
                "rust": True,
                "system_packages": ["curl", "git"],
                "java": {"distribution": "temurin", "version": "17"},
            },
        )
        self.assertEqual(normalize_coverage_setup(None), normalize_coverage_setup({}))
        self.assertEqual(normalize_coverage_assert_mode("strict"), {"default": "strict"})
        self.assertEqual(normalize_coverage_assert_mode(None), {"default": "enforce"})
        self.assertEqual(
            normalize_coverage_assert_mode({"default": "", "python": " warn ", "javascript": " "}),
            {"default": "enforce", "python": "warn"},
        )

    def test_normalize_coverage_helper_covers_string_inputs(self) -> None:
        self.assertEqual(
            normalize_coverage(
                {
                    "runner": " ",
                    "shell": "",
                    "command": "  qlty check  ",
                    "inputs": [{"format": "xml", "name": "coverage", "path": "coverage.xml"}],
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
            ),
            {
                "runner": "ubuntu-latest",
                "shell": "bash",
                "command": "qlty check",
                "inputs": [{"format": "xml", "name": "coverage", "path": "coverage.xml"}],
                "require_sources": ["source-a", "source-b"],
                "min_percent": 98.5,
                "assert_mode": {"default": "enforce", "python": "warn"},
                "evidence_note": "note",
                "setup": {
                    "python": "3.11",
                    "node": "20",
                    "go": "1.22",
                    "dotnet": "8",
                    "rust": True,
                    "system_packages": ["git", "curl"],
                    "java": {"distribution": "temurin", "version": "21"},
                },
            },
        )

    def test_normalize_codex_environment_helper_covers_string_inputs(self) -> None:
        self.assertEqual(
            normalize_codex_environment(None, verify_command="bash scripts/verify"),
            {
                "mode": "automatic",
                "verify_command": "bash scripts/verify",
                "auth_file": "~/.codex/auth.json",
                "network_profile": "unrestricted",
                "methods": "all",
                "runner_labels": ["self-hosted", "codex-trusted"],
            },
        )
        self.assertEqual(
            normalize_codex_environment(
                {
                    "mode": " manual ",
                    "verify_command": " python -m pytest ",
                    "auth_file": " ~/.codex/auth.json ",
                    "network_profile": " restricted ",
                    "methods": " changed ",
                    "runner_labels": [" self-hosted ", "self-hosted", "codex-trusted", ""],
                },
                verify_command="bash scripts/verify",
            ),
            {
                "mode": "manual",
                "verify_command": "python -m pytest",
                "auth_file": "~/.codex/auth.json",
                "network_profile": "restricted",
                "methods": "changed",
                "runner_labels": ["self-hosted", "codex-trusted"],
            },
        )

    def test_finalize_vendors_and_deep_merge_cover_string_inputs(self) -> None:
        self.assertEqual(
            finalize_vendors(
                {
                    "vendors": {"nested": {"left": 1}, "plain": 1},
                    "providers": {"nested": {"right": 2}, "extra": 3},
                }
            ),
            {"nested": {"left": 1, "right": 2}, "plain": 1, "extra": 3},
        )
        self.assertEqual(
            _deep_merge({"left": {"keep": 1}, "shared": 1}, {"left": {"add": 2}, "shared": 3, "extra": 4}),
            {"left": {"keep": 1, "add": 2}, "shared": 3, "extra": 4},
        )


