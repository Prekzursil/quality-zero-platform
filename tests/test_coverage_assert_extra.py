"""Test coverage assert -- payload, markdown, and entrypoint paths."""


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
    _render_md,
)

ROOT = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def _temporary_cwd(target: Path):
    """Handle temporary cwd."""
    previous = Path.cwd()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(previous)




class CoverageAssertExtraTests(unittest.TestCase):
    """CoverageAssertExtraTests."""

    def test_collect_inputs_build_payload_and_render_markdown(self) -> None:
        """Cover collect inputs build payload and render markdown."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            (root / "pkg" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "web").mkdir()
            (root / "web" / "app.ts").write_text(
                "export const ok = true;\n", encoding="utf-8"
            )
            xml_path = root / "coverage.xml"
            xml_path.write_text(
                '<coverage lines-valid="2" lines-covered="2"><class filename="pkg/main.py" /></coverage>',
                encoding="utf-8",
            )
            lcov_path = root / "coverage.lcov"
            lcov_path.write_text(
                "SF:web/app.ts\nDA:1,1\nLF:1\nLH:1\nend_of_record\n", encoding="utf-8"
            )
            args = Namespace(xml=[f"python={xml_path}"], lcov=[f"web={lcov_path}"])

            with _temporary_cwd(root):
                stats, covered_sources = _collect_coverage_inputs(args)

        payload = _build_payload(
            stats=stats,
            covered_sources=covered_sources,
            min_percent=100.0,
            branch_min_percent=85.0,
            status="pass",
            findings=[],
        )
        markdown = _render_md(payload)

        self.assertEqual([item.name for item in stats], ["python", "web"])
        self.assertEqual(sorted(covered_sources), ["pkg/main.py", "web/app.ts"])
        self.assertEqual(payload["status"], "pass")
        self.assertIn("pkg/main.py", markdown)
        self.assertIn("web/app.ts", markdown)
        self.assertIn("85.00%", markdown)

    def test_build_payload_rejects_positional_and_unexpected_keyword_arguments(
        self,
    ) -> None:
        """Cover build payload rejects positional and unexpected keyword arguments."""
        with self.assertRaisesRegex(TypeError, "keyword arguments only"):
            _build_payload("unexpected")

        with self.assertRaisesRegex(
            TypeError, "Unexpected _build_payload parameters: extra"
        ):
            _build_payload(
                stats=[],
                covered_sources=set(),
                min_percent=100.0,
                status="pass",
                findings=[],
                extra=True,
            )

    def test_main_requires_inputs_and_clamps_threshold(self) -> None:
        """Cover main requires inputs and clamps threshold."""
        with patch.object(
            assert_coverage_100,
            "_parse_args",
            return_value=Namespace(
                xml=[],
                lcov=[],
                require_source=[],
                min_percent=250.0,
                branch_min_percent="",
                out_json="coverage-100/coverage.json",
                out_md="coverage-100/coverage.md",
            ),
        ), self.assertRaisesRegex(SystemExit, "No coverage files were provided"):
            assert_coverage_100.main()

        stats = [
            CoverageStats(
                name="python",
                path="coverage.xml",
                covered=1,
                total=1,
                branch_covered=1,
                branch_total=1,
            )
        ]
        with patch.object(
            assert_coverage_100,
            "_parse_args",
            return_value=Namespace(
                xml=[],
                lcov=[],
                require_source=[],
                min_percent=250.0,
                branch_min_percent=90.0,
                out_json="coverage-100/custom.json",
                out_md="coverage-100/custom.md",
            ),
        ), patch.object(
            assert_coverage_100,
            "_collect_coverage_inputs",
            return_value=(stats, {"pkg/main.py"}),
        ), patch.object(
            assert_coverage_100, "write_report", return_value=0
        ) as write_report_mock:
            self.assertEqual(assert_coverage_100.main(), 0)

        payload = write_report_mock.call_args.args[0]
        self.assertEqual(payload["min_percent"], 100.0)
        self.assertEqual(payload["branch_min_percent"], 90.0)
        self.assertEqual(payload["status"], "pass")

    def test_coverage_stats_percent_and_render_markdown_cover_zero_totals_and_empty_sections(
        self,
    ) -> None:
        """Cover coverage stats percent and render markdown cover zero totals and empty sections."""
        self.assertEqual(
            CoverageStats(
                name="empty", path="coverage.xml", covered=0, total=0
            ).percent,
            100.0,
        )
        self.assertEqual(
            CoverageStats(
                name="empty",
                path="coverage.xml",
                covered=0,
                total=0,
                branch_covered=0,
                branch_total=0,
            ).branch_percent,
            100.0,
        )
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

    def test_component_markdown_line_renders_branch_coverage_when_present(self) -> None:
        """Cover component markdown line renders branch coverage when present."""
        markdown = _render_md(
            {
                "status": "pass",
                "min_percent": 100.0,
                "branch_min_percent": 85.0,
                "timestamp_utc": "2026-03-15T00:00:00+00:00",
                "components": [
                    {
                        "name": "python",
                        "percent": 100.0,
                        "covered": 10,
                        "total": 10,
                        "branch_percent": 90.0,
                        "branch_covered": 9,
                        "branch_total": 10,
                        "path": "coverage.xml",
                    }
                ],
                "covered_sources": ["pkg/main.py"],
                "findings": [],
            }
        )
        self.assertIn("branch=`90.00%` (9/10) from `coverage.xml`", markdown)

    def test_build_payload_includes_branch_percent_for_branch_tracked_components(
        self,
    ) -> None:
        """Cover build payload includes branch percent for branch tracked components."""
        payload = _build_payload(
            stats=[
                CoverageStats(
                    name="python",
                    path="coverage.xml",
                    covered=10,
                    total=10,
                    branch_covered=9,
                    branch_total=10,
                )
            ],
            covered_sources={"pkg/main.py"},
            min_percent=100.0,
            branch_min_percent=90.0,
            status="pass",
            findings=[],
        )

        self.assertEqual(payload["components"][0]["branch_percent"], 90.0)

    def test_script_entrypoint_handles_import_guard_and_report_failures(self) -> None:
        """Cover script entrypoint handles import guard and report failures."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xml_path = root / "coverage.xml"
            xml_path.write_text(
                '<coverage lines-valid="1" lines-covered="1"><class filename="app/main.py" /></coverage>',
                encoding="utf-8",
            )
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
            ), patch.object(sys, "path", trimmed_sys_path[:]), self.assertRaises(
                SystemExit
            ) as result:
                runpy.run_path(str(script_path), run_name="__main__")

            self.assertEqual(result.exception.code, 0)
            self.assertTrue(
                any(path.name == "out.json" for path in root.rglob("out.json"))
            )

        parsed_args = Namespace(
            xml=[],
            lcov=[],
            require_source=[],
            min_percent=100.0,
            branch_min_percent="",
            out_json="coverage-100/coverage.json",
            out_md="coverage-100/coverage.md",
        )
        collected_inputs = (
            [CoverageStats("python", "coverage.xml", 1, 1)],
            {"pkg/main.py"},
        )
        with (
            patch.object(assert_coverage_100, "_parse_args", return_value=parsed_args),
            patch.object(
                assert_coverage_100,
                "_collect_coverage_inputs",
                return_value=collected_inputs,
            ),
            patch.object(assert_coverage_100, "write_report", return_value=5),
        ):
            self.assertEqual(assert_coverage_100.main(), 5)
