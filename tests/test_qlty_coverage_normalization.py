from __future__ import absolute_import

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from scripts.quality import normalize_coverage_for_qlty


class QltyCoverageNormalizationTests(unittest.TestCase):
    def test_normalize_xml_report_rewrites_nested_source_roots_to_repo_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_dir = root / "repo"
            out_dir = root / "out"
            (repo_dir / "backend" / "app").mkdir(parents=True)
            (repo_dir / "ui" / "src").mkdir(parents=True)
            (repo_dir / "backend" / "app" / "api.py").write_text("print('ok')\n", encoding="utf-8")
            (repo_dir / "ui" / "src" / "App.tsx").write_text("export const App = () => null;\n", encoding="utf-8")

            backend_report = repo_dir / "backend" / "coverage.xml"
            backend_report.write_text(
                (
                    '<?xml version="1.0" ?>\n'
                    '<coverage lines-valid="1" lines-covered="1" branches-valid="0" branches-covered="0">'
                    f"<sources><source>{repo_dir.as_posix()}</source></sources>"
                    '<packages><package name="app"><classes>'
                    '<class filename="backend/app/api.py" line-rate="1" branch-rate="1" />'
                    "</classes></package></packages></coverage>"
                ),
                encoding="utf-8",
            )

            frontend_report = repo_dir / "ui" / "coverage.xml"
            frontend_report.write_text(
                (
                    '<?xml version="1.0" ?>\n'
                    '<coverage lines-valid="1" lines-covered="1" branches-valid="0" branches-covered="0">'
                    f"<sources><source>{(repo_dir / 'ui').as_posix()}</source></sources>"
                    '<packages><package name="src"><classes>'
                    '<class filename="src/App.tsx" line-rate="1" branch-rate="1" />'
                    "</classes></package></packages></coverage>"
                ),
                encoding="utf-8",
            )

            payload = normalize_coverage_for_qlty.normalize_reports(
                ["backend/coverage.xml", "ui/coverage.xml"],
                repo_dir=repo_dir,
                out_dir=out_dir,
            )

            self.assertEqual(len(payload), 2)
            normalized_frontend = ElementTree.parse(Path(payload[1]["normalized"])).getroot()
            frontend_class = next(element for element in normalized_frontend.iter() if element.get("filename"))
            frontend_source = next(
                element for element in normalized_frontend.iter() if element.tag.rsplit("}", 1)[-1] == "source"
            )
            self.assertEqual(frontend_class.get("filename"), "ui/src/App.tsx")
            self.assertEqual(frontend_source.text, repo_dir.as_posix())

            normalized_backend = ElementTree.parse(Path(payload[0]["normalized"])).getroot()
            backend_class = next(element for element in normalized_backend.iter() if element.get("filename"))
            self.assertEqual(backend_class.get("filename"), "backend/app/api.py")

    def test_normalize_lcov_report_rewrites_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_dir = root / "repo"
            out_dir = root / "out"
            (repo_dir / "src").mkdir(parents=True)
            source_path = repo_dir / "src" / "main.ts"
            source_path.write_text("export const value = 1;\n", encoding="utf-8")
            report_path = repo_dir / "coverage" / "lcov.info"
            report_path.parent.mkdir(parents=True)
            report_path.write_text(f"SF:{source_path.as_posix()}\nLF:1\nLH:1\nend_of_record\n", encoding="utf-8")

            payload = normalize_coverage_for_qlty.normalize_reports(
                ["coverage/lcov.info"],
                repo_dir=repo_dir,
                out_dir=out_dir,
            )

            self.assertEqual(payload[0]["format"], "lcov")
            self.assertEqual(payload[0]["rewritten_paths"], 1)
            normalized_text = Path(payload[0]["normalized"]).read_text(encoding="utf-8")
            self.assertIn("SF:src/main.ts", normalized_text)

    def test_main_prints_machine_readable_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_dir = root / "repo"
            out_dir = root / "out"
            (repo_dir / "src").mkdir(parents=True)
            (repo_dir / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            report_path = repo_dir / "coverage" / "lcov.info"
            report_path.parent.mkdir(parents=True)
            report_path.write_text("SF:src/main.py\nLF:1\nLH:1\nend_of_record\n", encoding="utf-8")

            argv = [
                "normalize_coverage_for_qlty.py",
                "--repo-dir",
                str(repo_dir),
                "--out-dir",
                str(out_dir),
                "coverage/lcov.info",
            ]
            with patch("sys.argv", argv), patch("sys.stdout", new=io.StringIO()) as stdout:
                self.assertEqual(normalize_coverage_for_qlty.main(), 0)

            payload = json.loads(stdout.getvalue())
            self.assertTrue(str(payload[0]["normalized"]).startswith(str(out_dir.resolve())))
            self.assertTrue(str(payload[0]["normalized"]).endswith("report-1.info"))

    def test_existing_candidate_covers_fallback_and_empty_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_dir = root / "repo"
            (repo_dir / "src").mkdir(parents=True)
            (repo_dir / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            with patch.object(normalize_coverage_for_qlty, "_coverage_source_candidates", return_value=[]):
                with patch.object(normalize_coverage_for_qlty, "_normalize_source_path", return_value="src/main.py"):
                    previous = Path.cwd()
                    try:
                        import os

                        os.chdir(repo_dir)
                        self.assertEqual(
                            normalize_coverage_for_qlty._existing_candidate("ignored", []),
                            "src/main.py",
                        )
                    finally:
                        os.chdir(previous)
                with patch.object(normalize_coverage_for_qlty, "_normalize_source_path", return_value=""):
                    self.assertEqual(normalize_coverage_for_qlty._existing_candidate("", []), "")

    def test_normalize_reports_copies_unknown_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_dir = root / "repo"
            out_dir = root / "out"
            report_path = repo_dir / "coverage" / "custom.txt"
            report_path.parent.mkdir(parents=True)
            report_path.write_text("custom coverage payload\n", encoding="utf-8")

            payload = normalize_coverage_for_qlty.normalize_reports(
                ["coverage/custom.txt"],
                repo_dir=repo_dir,
                out_dir=out_dir,
            )

            self.assertEqual(payload[0]["format"], "copy")
            self.assertEqual(payload[0]["rewritten_paths"], 0)
            self.assertEqual(Path(payload[0]["normalized"]).read_text(encoding="utf-8"), "custom coverage payload\n")
