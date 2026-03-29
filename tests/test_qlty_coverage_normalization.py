"""Test qlty coverage normalization."""

from __future__ import absolute_import

import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Tuple
from unittest.mock import patch

from scripts.quality import normalize_coverage_for_qlty


class QltyCoverageNormalizationTests(unittest.TestCase):
    """Regression coverage for QLTY coverage path normalization helpers."""

    @staticmethod
    def _make_repo_fixture(root: Path) -> Tuple[Path, Path]:
        """Create a mixed-root sample repo for coverage normalization tests."""
        repo_dir = root / "repo"
        out_dir = repo_dir / ".normalized"
        (repo_dir / "backend" / "app").mkdir(parents=True)
        (repo_dir / "ui" / "src").mkdir(parents=True)
        (repo_dir / "backend" / "app" / "api.py").write_text(
            "print('ok')\n",
            encoding="utf-8",
        )
        (repo_dir / "ui" / "src" / "App.tsx").write_text(
            "export const App = () => null;\n",
            encoding="utf-8",
        )
        return repo_dir, out_dir

    @staticmethod
    def _write_xml_report(
        report_path: Path,
        *,
        source_root: str,
        filename: str,
    ) -> None:
        """Write a minimal Cobertura-like XML payload for normalization tests."""
        report_path.write_text(
            (
                '<?xml version="1.0" ?>\n'
                '<coverage lines-valid="1" lines-covered="1" '
                'branches-valid="0" branches-covered="0">'
                f"<sources><source>{source_root}</source></sources>"
                '<packages><package name="app"><classes>'
                f'<class filename="{filename}" line-rate="1" branch-rate="1" />'
                "</classes></package></packages></coverage>"
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _normalized_text(payload_entry: Dict[str, object]) -> str:
        """Read one normalized coverage artifact from a manifest entry."""
        normalized = payload_entry["normalized"]
        if not isinstance(normalized, str):
            raise AssertionError(
                "Normalized coverage manifest entry must be a string path."
            )
        return Path(normalized).read_text(encoding="utf-8")

    def _sample_xml_payload(
        self,
        repo_dir: Path,
        out_dir: Path,
    ) -> List[Dict[str, object]]:
        """Build and normalize paired backend/frontend XML reports."""
        self._write_xml_report(
            repo_dir / "backend" / "coverage.xml",
            source_root=repo_dir.as_posix(),
            filename="backend/app/api.py",
        )
        self._write_xml_report(
            repo_dir / "ui" / "coverage.xml",
            source_root=(repo_dir / "ui").as_posix(),
            filename="src/App.tsx",
        )
        return normalize_coverage_for_qlty.normalize_reports(
            ["backend/coverage.xml", "ui/coverage.xml"],
            repo_dir=repo_dir,
            out_dir=out_dir,
        )

    def test_normalize_xml_report_rewrites_frontend_paths(self) -> None:
        """Frontend XML reports should normalize source roots and filenames."""
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir, out_dir = self._make_repo_fixture(Path(temp_dir))
            payload = self._sample_xml_payload(repo_dir, out_dir)

            self.assertEqual(len(payload), 2)
            frontend_text = self._normalized_text(payload[1])
            self.assertIn('filename="ui/src/App.tsx"', frontend_text)
            self.assertIn(
                f"<source>{repo_dir.as_posix()}</source>",
                frontend_text,
            )

    def test_normalize_xml_report_preserves_backend_repo_relative_paths(self) -> None:
        """Backend XML reports should keep repo-relative file names intact."""
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir, out_dir = self._make_repo_fixture(Path(temp_dir))
            payload = self._sample_xml_payload(repo_dir, out_dir)

            self.assertEqual(len(payload), 2)
            backend_text = self._normalized_text(payload[0])
            self.assertIn('filename="backend/app/api.py"', backend_text)

    def test_normalize_lcov_report_rewrites_absolute_paths(self) -> None:
        """LCOV records should rewrite absolute paths to repo-relative ones."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_dir = root / "repo"
            out_dir = repo_dir / ".normalized"
            (repo_dir / "src").mkdir(parents=True)
            source_path = repo_dir / "src" / "main.ts"
            source_path.write_text("export const value = 1;\n", encoding="utf-8")
            report_path = repo_dir / "coverage" / "lcov.info"
            report_path.parent.mkdir(parents=True)
            report_path.write_text(
                f"SF:{source_path.as_posix()}\nLF:1\nLH:1\nend_of_record\n",
                encoding="utf-8",
            )

            payload = normalize_coverage_for_qlty.normalize_reports(
                ["coverage/lcov.info"],
                repo_dir=repo_dir,
                out_dir=out_dir,
            )

            self.assertEqual(payload[0]["format"], "lcov")
            self.assertEqual(payload[0]["rewritten_paths"], 1)
            normalized_text = self._normalized_text(payload[0])
            self.assertIn("SF:src/main.ts", normalized_text)

    def test_main_prints_machine_readable_payload(self) -> None:
        """The CLI entrypoint should print a JSON manifest for downstream use."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_dir = root / "repo"
            out_dir = repo_dir / ".normalized"
            (repo_dir / "src").mkdir(parents=True)
            (repo_dir / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            report_path = repo_dir / "coverage" / "lcov.info"
            report_path.parent.mkdir(parents=True)
            report_path.write_text(
                "SF:src/main.py\nLF:1\nLH:1\nend_of_record\n",
                encoding="utf-8",
            )

            argv = [
                "normalize_coverage_for_qlty.py",
                "--repo-dir",
                str(repo_dir),
                "--out-dir",
                ".normalized",
                "coverage/lcov.info",
            ]
            with patch("sys.argv", argv), patch(
                "sys.stdout",
                new=io.StringIO(),
            ) as stdout:
                self.assertEqual(normalize_coverage_for_qlty.main(), 0)

            payload = json.loads(stdout.getvalue())
            normalized = payload[0]["normalized"]
            self.assertIsInstance(normalized, str)
            self.assertTrue(normalized.startswith(str(out_dir.resolve())))
            self.assertTrue(normalized.endswith("report-1.info"))

    def test_existing_candidate_covers_fallback_and_empty_paths(self) -> None:
        """Fallback normalization should cover both existing and empty candidates."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_dir = root / "repo"
            (repo_dir / "src").mkdir(parents=True)
            (repo_dir / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            with patch.object(
                normalize_coverage_for_qlty,
                "_coverage_source_candidates",
                return_value=[],
            ):
                with patch.object(
                    normalize_coverage_for_qlty,
                    "_normalize_source_path",
                    return_value="src/main.py",
                ):
                    previous = Path.cwd()
                    try:
                        import os

                        os.chdir(repo_dir)
                        self.assertEqual(
                            normalize_coverage_for_qlty._existing_candidate(
                                "ignored",
                                [],
                            ),
                            "src/main.py",
                        )
                    finally:
                        os.chdir(previous)
                with patch.object(
                    normalize_coverage_for_qlty,
                    "_normalize_source_path",
                    return_value="",
                ):
                    self.assertEqual(
                        normalize_coverage_for_qlty._existing_candidate("", []),
                        "",
                    )

    def test_normalize_reports_copies_unknown_formats(self) -> None:
        """Unknown report formats should still be copied into the temp artifact set."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_dir = root / "repo"
            out_dir = repo_dir / ".normalized"
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
            self.assertEqual(
                self._normalized_text(payload[0]),
                "custom coverage payload\n",
            )

    def test_normalize_reports_rejects_workspace_escapes(self) -> None:
        """Normalization inputs and outputs must stay inside the repo workspace."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_dir = root / "repo"
            (repo_dir / "coverage").mkdir(parents=True)
            (repo_dir / "coverage" / "lcov.info").write_text(
                "SF:src/main.py\nLF:1\nLH:1\nend_of_record\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "escapes normalized coverage workspace",
            ):
                normalize_coverage_for_qlty.normalize_reports(
                    ["../outside.info"],
                    repo_dir=repo_dir,
                    out_dir=repo_dir / ".normalized",
                )

            with self.assertRaisesRegex(
                ValueError,
                "escapes normalized coverage workspace",
            ):
                normalize_coverage_for_qlty.normalize_reports(
                    ["coverage/lcov.info"],
                    repo_dir=repo_dir,
                    out_dir=root / "outside",
                )

    def test_normalized_text_rejects_non_string_manifest_paths(self) -> None:
        """Manifest helpers should reject malformed normalized path entries."""
        with self.assertRaisesRegex(
            AssertionError,
            "Normalized coverage manifest entry must be a string path.",
        ):
            self._normalized_text({"normalized": 1})
