from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.quality.assert_coverage_100 import CoverageStats, coverage_sources_from_lcov, coverage_sources_from_xml, evaluate


class CoverageAssertTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
