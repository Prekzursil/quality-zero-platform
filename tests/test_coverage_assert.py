from __future__ import absolute_import

import contextlib
import os
import runpy
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from scripts.quality import assert_coverage_100
from scripts.quality.assert_coverage_100 import (
    CoverageStats,
    _build_payload,
    _collect_coverage_inputs,
    _find_missing_required_sources,
    _is_tests_only_report,
    _matches_required_source,
    _normalize_source_path,
    _required_source_findings,
    _render_md,
    parse_coverage_xml,
    parse_lcov,
    parse_named_path,
    coverage_sources_from_lcov,
    coverage_sources_from_xml,
    evaluate,
)

ROOT = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def _temporary_cwd(target: Path):
    previous = Path.cwd()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(previous)


class CoverageAssertTests(unittest.TestCase):
    def test_parse_named_path_validates_expected_format(self) -> None:
        name, path = parse_named_path("platform=coverage.xml")
        self.assertEqual(name, "platform")
        self.assertEqual(path, Path("coverage.xml"))
        with self.assertRaisesRegex(ValueError, "Expected format"):
            parse_named_path("coverage.xml")

    def test_parse_coverage_xml_supports_summary_and_line_hit_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_xml = root / "summary.xml"
            summary_xml.write_text('<coverage lines-valid="5" lines-covered="4"></coverage>', encoding="utf-8")
            summary_stats = parse_coverage_xml("summary", summary_xml)

            fallback_xml = root / "fallback.xml"
            fallback_xml.write_text(
                '<coverage><line hits="1" /><line hits="0" /><line hits="2" /></coverage>',
                encoding="utf-8",
            )
            fallback_stats = parse_coverage_xml("fallback", fallback_xml)

        self.assertEqual((summary_stats.covered, summary_stats.total), (4, 5))
        self.assertEqual((fallback_stats.covered, fallback_stats.total), (2, 3))

    def test_parse_lcov_counts_lines_found_and_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lcov_path = root / "coverage.lcov"
            lcov_path.write_text("LF:4\nLH:3\nLF:2\nLH:1\n", encoding="utf-8")
            stats = parse_lcov("frontend", lcov_path)

        self.assertEqual((stats.covered, stats.total), (4, 6))

    def test_coverage_sources_from_xml_and_lcov_are_workspace_relative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xml_path = root / "coverage.xml"
            lcov_path = root / "coverage.lcov"
            xml_path.write_text(
                '<coverage lines-valid="2" lines-covered="2"><class filename="app/main.py" /></coverage>',
                encoding="utf-8",
            )
            lcov_path.write_text("SF:frontend/src/app.ts\nDA:1,1\nLF:1\nLH:1\nend_of_record\n", encoding="utf-8")

            xml_sources = coverage_sources_from_xml(xml_path)
            lcov_sources = coverage_sources_from_lcov(lcov_path)

        self.assertIn("app/main.py", xml_sources)
        self.assertIn("frontend/src/app.ts", lcov_sources)

    def test_evaluate_flags_missing_required_sources_and_honors_threshold(self) -> None:
        stats = [CoverageStats(name="python", path="coverage.xml", covered=9, total=10)]

        status, findings = evaluate(
            stats,
            100.0,
            required_sources=["app/main.py"],
            reported_sources={"tests/test_main.py"},
        )

        self.assertEqual(status, "fail")
        self.assertTrue(any("coverage below 100.00%" in item for item in findings))
        self.assertTrue(any("missing required source path: app/main.py" in item for item in findings))
        self.assertTrue(any("tests/ paths" in item for item in findings))

        ok_status, ok_findings = evaluate(
            stats,
            90.0,
            required_sources=["app/main.py"],
            reported_sources={"app/main.py"},
        )

        self.assertEqual(ok_status, "pass")
        self.assertEqual(ok_findings, [])

    def test_source_normalization_and_required_source_helpers_cover_edge_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with _temporary_cwd(root):
                workspace_root = Path.cwd().resolve(strict=False).as_posix().rstrip("/")
                self.assertEqual(_normalize_source_path(f"{workspace_root}/src/main.py"), "src/main.py")
                self.assertEqual(_normalize_source_path("./src//main.py"), "src/main.py")
                self.assertEqual(_normalize_source_path("."), "")
                self.assertEqual(_normalize_source_path(workspace_root), "")
                self.assertFalse(_matches_required_source("src/main.py", ""))
                self.assertTrue(_matches_required_source("src/main.py", "src"))
                self.assertEqual(
                    _find_missing_required_sources({"src/main.py"}, ["", "src", "frontend/app.ts"]),
                    ["frontend/app.ts"],
                )
                self.assertTrue(_is_tests_only_report({"tests/test_main.py"}))
                self.assertEqual(
                    _required_source_findings({"tests/test_main.py"}, ["src/main.py"]),
                    [
                        "coverage inputs only reference tests/ paths; first-party sources are missing.",
                        "missing required source path: src/main.py",
                    ],
                )

    def test_collect_inputs_build_payload_and_render_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xml_path = root / "coverage.xml"
            xml_path.write_text(
                '<coverage lines-valid="2" lines-covered="2"><class filename="pkg/main.py" /></coverage>',
                encoding="utf-8",
            )
            lcov_path = root / "coverage.lcov"
            lcov_path.write_text("SF:web/app.ts\nDA:1,1\nLF:1\nLH:1\nend_of_record\n", encoding="utf-8")
            args = Namespace(xml=[f"python={xml_path}"], lcov=[f"web={lcov_path}"])

            with _temporary_cwd(root):
                stats, covered_sources = _collect_coverage_inputs(args)

        payload = _build_payload(
            stats=stats,
            covered_sources=covered_sources,
            min_percent=100.0,
            status="pass",
            findings=[],
        )
        markdown = _render_md(payload)

        self.assertEqual([item.name for item in stats], ["python", "web"])
        self.assertEqual(sorted(covered_sources), ["pkg/main.py", "web/app.ts"])
        self.assertEqual(payload["status"], "pass")
        self.assertIn("pkg/main.py", markdown)
        self.assertIn("web/app.ts", markdown)

    def test_main_requires_inputs_and_clamps_threshold(self) -> None:
        with patch.object(assert_coverage_100, "_parse_args", return_value=Namespace(
            xml=[],
            lcov=[],
            require_source=[],
            min_percent=250.0,
            out_json="coverage-100/coverage.json",
            out_md="coverage-100/coverage.md",
        )):
            with self.assertRaisesRegex(SystemExit, "No coverage files were provided"):
                assert_coverage_100.main()

        stats = [CoverageStats(name="python", path="coverage.xml", covered=1, total=1)]
        with patch.object(assert_coverage_100, "_parse_args", return_value=Namespace(
            xml=[],
            lcov=[],
            require_source=[],
            min_percent=250.0,
            out_json="coverage-100/custom.json",
            out_md="coverage-100/custom.md",
        )), patch.object(assert_coverage_100, "_collect_coverage_inputs", return_value=(stats, {"pkg/main.py"})), patch.object(
            assert_coverage_100, "write_report", return_value=0
        ) as write_report_mock, patch.object(assert_coverage_100, "safe_output_path", side_effect=lambda raw, _fallback: Path(raw)):
            self.assertEqual(assert_coverage_100.main(), 0)

        payload = write_report_mock.call_args.args[0]
        self.assertEqual(payload["min_percent"], 100.0)
        self.assertEqual(payload["status"], "pass")

    def test_coverage_stats_percent_and_render_markdown_cover_zero_totals_and_empty_sections(self) -> None:
        self.assertEqual(CoverageStats(name="empty", path="coverage.xml", covered=0, total=0).percent, 100.0)
        markdown = _render_md(
            {
                "status": "pass",
                "min_percent": 100.0,
                "timestamp_utc": "2026-03-15T00:00:00+00:00",
                "components": [],
                "covered_sources": [],
                "findings": [],
            }
        )
        self.assertIn("- None", markdown)

    def test_script_entrypoint_handles_import_guard_and_report_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xml_path = root / "coverage.xml"
            xml_path.write_text('<coverage lines-valid="1" lines-covered="1"><class filename="app/main.py" /></coverage>', encoding="utf-8")
            script_path = ROOT / "scripts" / "quality" / "assert_coverage_100.py"
            root_text = str(ROOT)
            trimmed_sys_path = [item for item in sys.path if item != root_text]

            with _temporary_cwd(root), patch.object(
                sys,
                "argv",
                [
                    str(script_path),
                    "--xml",
                    f"python={xml_path}",
                    "--out-json",
                    "coverage-100/out.json",
                    "--out-md",
                    "coverage-100/out.md",
                ],
            ), patch.object(sys, "path", trimmed_sys_path[:]):
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(script_path), run_name="__main__")

            self.assertEqual(result.exception.code, 0)
            self.assertTrue(any(path.name == "out.json" for path in root.rglob("out.json")))

        parsed_args = Namespace(
            xml=[],
            lcov=[],
            require_source=[],
            min_percent=100.0,
            out_json="coverage-100/coverage.json",
            out_md="coverage-100/coverage.md",
        )
        collected_inputs = ([CoverageStats("python", "coverage.xml", 1, 1)], {"pkg/main.py"})
        with (
            patch.object(assert_coverage_100, "_parse_args", return_value=parsed_args),
            patch.object(assert_coverage_100, "_collect_coverage_inputs", return_value=collected_inputs),
            patch.object(assert_coverage_100, "write_report", return_value=5),
            patch.object(assert_coverage_100, "safe_output_path", side_effect=lambda raw, _fallback: Path(raw)),
        ):
            self.assertEqual(assert_coverage_100.main(), 5)

