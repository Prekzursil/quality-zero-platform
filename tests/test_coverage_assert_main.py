"""Focused coverage-assert evaluation and entrypoint tests."""

from __future__ import absolute_import

import runpy
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from scripts.quality import assert_coverage_100
from scripts.quality.assert_coverage_100 import (
    CoverageEvaluationRequest,
    CoverageStats,
    _existing_repo_file_candidate,
    _find_missing_required_sources,
    _is_tests_only_report,
    _matches_required_source,
    _normalize_source_path,
    _required_source_findings,
    _should_track_coverage_source,
    evaluate,
)
from tests.test_coverage_assert import ROOT, _temporary_cwd


class CoverageAssertMainTests(unittest.TestCase):
    """Coverage-assert evaluation and entrypoint tests."""

    def test_evaluate_flags_missing_required_sources(self) -> None:
        """Cover missing required-source findings."""
        stats = [CoverageStats(name="python", path="coverage.xml", covered=9, total=10)]
        status, findings = evaluate(
            stats,
            CoverageEvaluationRequest(
                min_percent=100.0,
                required_sources=["app/main.py"],
                reported_sources={"tests/test_main.py"},
            ),
        )
        self.assertEqual(status, "fail")
        self.assertTrue(any("coverage below 100.00%" in item for item in findings))
        self.assertTrue(
            any("missing required source path: app/main.py" in item for item in findings)
        )
        self.assertTrue(any("tests/ paths" in item for item in findings))

    def test_evaluate_passes_when_threshold_and_sources_match(self) -> None:
        """Cover successful evaluation with matching thresholds and sources."""
        stats = [CoverageStats(name="python", path="coverage.xml", covered=9, total=10)]
        status, findings = evaluate(
            stats,
            CoverageEvaluationRequest(
                min_percent=90.0,
                required_sources=["app/main.py"],
                reported_sources={"app/main.py"},
            ),
        )
        self.assertEqual(status, "pass")
        self.assertEqual(findings, [])

    def test_evaluate_handles_branch_threshold_and_missing_branch_data(self) -> None:
        """Cover branch-threshold failures and missing branch data."""
        branch_stats = [
            CoverageStats(
                name="python",
                path="coverage.xml",
                covered=10,
                total=10,
                branch_covered=5,
                branch_total=10,
            )
        ]
        branch_status, branch_findings = evaluate(
            branch_stats,
            CoverageEvaluationRequest(
                min_percent=100.0,
                branch_min_percent=80.0,
                required_sources=["app/main.py"],
                reported_sources={"app/main.py"},
            ),
        )
        self.assertEqual(branch_status, "fail")
        self.assertTrue(
            any("branch coverage below 80.00%" in item for item in branch_findings)
        )

        missing_branch_status, missing_branch_findings = evaluate(
            [
                CoverageStats(
                    name="no-branch",
                    path="coverage.xml",
                    covered=10,
                    total=10,
                    branch_covered=0,
                    branch_total=0,
                )
            ],
            CoverageEvaluationRequest(
                min_percent=100.0,
                branch_min_percent=100.0,
                required_sources=["app/main.py"],
                reported_sources={"app/main.py"},
            ),
        )
        self.assertEqual(missing_branch_status, "fail")
        self.assertTrue(
            any(
                "branch coverage data missing" in item
                for item in missing_branch_findings
            )
        )

    def test_source_normalization_handles_workspace_and_external_paths(self) -> None:
        """Cover source normalization for workspace and external paths."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as ext:
            root = Path(tmp)
            external_file = Path(ext) / "external.py"
            external_file.write_text("print('external')\n", encoding="utf-8")
            (root / "src").mkdir(parents=True)
            (root / "src" / "main.py").write_text("print('main')\n", encoding="utf-8")
            with _temporary_cwd(root):
                workspace_root = Path.cwd().resolve(strict=False).as_posix().rstrip("/")
                self.assertEqual(
                    _normalize_source_path(f"{workspace_root}/src/main.py"),
                    "src/main.py",
                )
                self.assertEqual(_normalize_source_path("./src//main.py"), "src/main.py")
                self.assertEqual(_normalize_source_path("."), "")
                self.assertEqual(_normalize_source_path("./"), "")
                self.assertEqual(_normalize_source_path(workspace_root), "")
                self.assertEqual(
                    _normalize_source_path(external_file.as_posix()),
                    external_file.resolve(strict=False).as_posix(),
                )
                self.assertEqual(_existing_repo_file_candidate(""), "")
                self.assertEqual(
                    _existing_repo_file_candidate("repo/src/main.py"), "src/main.py"
                )

    def test_required_source_helpers_cover_edge_cases(self) -> None:
        """Cover required-source helper edge cases."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir(parents=True)
            (root / "src" / "main.py").write_text("print('main')\n", encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "shim.js").write_text(
                "export default true;\n", encoding="utf-8"
            )
            with _temporary_cwd(root):
                self.assertFalse(_should_track_coverage_source(""))
                self.assertFalse(_should_track_coverage_source("node_modules/shim.js"))
                self.assertFalse(_matches_required_source("src/main.py", ""))
                self.assertTrue(_matches_required_source("src/main.py", "src"))
                self.assertEqual(
                    _find_missing_required_sources(
                        {"src/main.py"}, ["", "src", "frontend/app.ts"]
                    ),
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

    def test_script_entrypoint_handles_import_guard(self) -> None:
        """Cover the direct script entrypoint import-guard path."""
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
            self.assertTrue(any(path.name == "out.json" for path in root.rglob("out.json")))

    def test_main_propagates_write_report_failures(self) -> None:
        """Cover write-report failures from the main path."""
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
