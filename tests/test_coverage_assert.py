"""Test coverage assert."""

from __future__ import absolute_import

import contextlib
import os
import tempfile
import unittest
from pathlib import Path

from scripts.quality.assert_coverage_100 import (
    CoverageEvaluationRequest,
    CoverageStats,
    _find_missing_required_sources,
    _is_tests_only_report,
    _matches_required_source,
    _normalize_source_path,
    _required_source_findings,
    parse_coverage_xml,
    parse_lcov,
    parse_named_path,
    coverage_sources_from_lcov,
    coverage_sources_from_xml,
    evaluate,
)
from scripts.quality.coverage_support import (
    _existing_repo_file_candidate,
    _should_track_coverage_source,
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


class CoverageAssertTests(unittest.TestCase):
    """Coverage Assert Tests."""

    def test_parse_named_path_validates_expected_format(self) -> None:
        """Cover parse named path validates expected format."""
        name, path = parse_named_path("platform=coverage.xml")
        self.assertEqual(name, "platform")
        self.assertEqual(path, Path("coverage.xml"))
        with self.assertRaisesRegex(ValueError, "Expected format"):
            parse_named_path("coverage.xml")

    def test_parse_coverage_xml_supports_summary_and_line_hit_fallback(self) -> None:
        """Cover parse coverage xml supports summary and line hit fallback."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_xml = root / "summary.xml"
            summary_xml.write_text(
                '<coverage lines-valid="5" lines-covered="4" branches-valid="6" branches-covered="3"></coverage>',
                encoding="utf-8",
            )
            summary_stats = parse_coverage_xml("summary", summary_xml)

            fallback_xml = root / "fallback.xml"
            fallback_xml.write_text(
                '<coverage><line hits="1" /><line hits="0" /><line hits="2" /></coverage>',
                encoding="utf-8",
            )
            fallback_stats = parse_coverage_xml("fallback", fallback_xml)

        self.assertEqual((summary_stats.covered, summary_stats.total), (4, 5))
        self.assertEqual(
            (summary_stats.branch_covered, summary_stats.branch_total), (3, 6)
        )
        self.assertEqual((fallback_stats.covered, fallback_stats.total), (2, 3))

    def test_parse_lcov_counts_lines_found_and_hit(self) -> None:
        """Cover parse lcov counts lines found and hit."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lcov_path = root / "coverage.lcov"
            (root / "src").mkdir()
            (root / "src" / "main.cpp").write_text(
                "int main() { return 0; }\n", encoding="utf-8"
            )
            lcov_path.write_text(
                "\n".join(
                    [
                        f"SF:{root.as_posix()}/build/CMakeFiles/app.dir/src/main.cpp",
                        "LF:4",
                        "LH:3",
                        "BRF:6",
                        "BRH:4",
                        "end_of_record",
                        f"SF:{root.as_posix()}/build/_deps/googletest-src/googletest/src/gtest.cc",
                        "LF:2",
                        "LH:1",
                        "end_of_record",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with _temporary_cwd(root):
                stats = parse_lcov("frontend", lcov_path)

        self.assertEqual((stats.covered, stats.total), (3, 4))
        self.assertEqual((stats.branch_covered, stats.branch_total), (4, 6))

    def test_coverage_sources_from_xml_and_lcov_are_workspace_relative(self) -> None:
        """Cover coverage sources from xml and lcov are workspace relative."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts" / "quality").mkdir(parents=True)
            (root / "scripts" / "quality" / "check_required_checks.py").write_text(
                "print('ok')\n", encoding="utf-8"
            )
            (root / "src").mkdir()
            (root / "src" / "app.ts").write_text(
                "export const ok = true;\n", encoding="utf-8"
            )
            xml_path = root / "coverage.xml"
            lcov_path = root / "coverage.lcov"
            xml_path.write_text(
                (
                    '<coverage lines-valid="2" lines-covered="2">'
                    f"<sources><source>{root.as_posix()}/scripts</source></sources>"
                    '<class filename="quality/check_required_checks.py" />'
                    "</coverage>"
                ),
                encoding="utf-8",
            )
            lcov_path.write_text(
                "\n".join(
                    [
                        f"SF:{root.as_posix()}/build/CMakeFiles/app.dir/src/app.ts",
                        "DA:1,1",
                        "LF:1",
                        "LH:1",
                        "end_of_record",
                        f"SF:{root.as_posix()}/build/_deps/googletest-src/googletest/src/gtest.cc",
                        "DA:1,1",
                        "LF:1",
                        "LH:1",
                        "end_of_record",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with _temporary_cwd(root):
                xml_sources = coverage_sources_from_xml(xml_path)
                lcov_sources = coverage_sources_from_lcov(lcov_path)

        self.assertIn("scripts/quality/check_required_checks.py", xml_sources)
        self.assertIn("src/app.ts", lcov_sources)
        self.assertNotIn(
            "build/_deps/googletest-src/googletest/src/gtest.cc", lcov_sources
        )

    def test_evaluate_flags_missing_required_sources_and_honors_threshold(self) -> None:
        """Cover evaluate flags missing required sources and honors threshold."""
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
            any(
                "missing required source path: app/main.py" in item for item in findings
            )
        )
        self.assertTrue(any("tests/ paths" in item for item in findings))

        ok_status, ok_findings = evaluate(
            stats,
            CoverageEvaluationRequest(
                min_percent=90.0,
                required_sources=["app/main.py"],
                reported_sources={"app/main.py"},
            ),
        )

        self.assertEqual(ok_status, "pass")
        self.assertEqual(ok_findings, [])

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

    def test_source_normalization_and_required_source_helpers_cover_edge_cases(
        self,
    ) -> None:
        """Cover source normalization and required source helpers cover edge cases."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with tempfile.TemporaryDirectory() as external_tmp:
                external_root = Path(external_tmp)
                external_file = external_root / "external.py"
                external_file.write_text("print('external')\n", encoding="utf-8")
                (root / "src").mkdir(parents=True)
                (root / "src" / "main.py").write_text(
                    "print('main')\n", encoding="utf-8"
                )
                (root / "node_modules").mkdir()
                (root / "node_modules" / "shim.js").write_text(
                    "export default true;\n", encoding="utf-8"
                )
            with _temporary_cwd(root):
                workspace_root = Path.cwd().resolve(strict=False).as_posix().rstrip("/")
                self.assertEqual(
                    _normalize_source_path(f"{workspace_root}/src/main.py"),
                    "src/main.py",
                )
                self.assertEqual(_normalize_source_path("./"), "")
                self.assertEqual(
                    _normalize_source_path("./src//main.py"), "src/main.py"
                )
                self.assertEqual(_normalize_source_path("."), "")
                self.assertEqual(_normalize_source_path(workspace_root), "")
                self.assertEqual(
                    _normalize_source_path(external_file.as_posix()),
                    external_file.resolve(strict=False).as_posix(),
                )
                self.assertEqual(_existing_repo_file_candidate(""), "")
                self.assertEqual(
                    _existing_repo_file_candidate("repo/src/main.py"), "src/main.py"
                )
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

