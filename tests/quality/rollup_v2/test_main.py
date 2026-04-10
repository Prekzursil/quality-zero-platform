"""Tests for __main__.py CLI entrypoint (per design §A.8 + Phase 13)."""
from __future__ import absolute_import

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


class ParseArgsTests(unittest.TestCase):
    """Test CLI argument parsing."""

    def test_required_args_only(self) -> None:
        from scripts.quality.rollup_v2.__main__ import parse_args

        with patch(
            "sys.argv",
            [
                "__main__.py",
                "--artifacts-dir", "/tmp/artifacts",
                "--output-dir", "/tmp/output",
                "--repo", "owner/repo",
                "--sha", "abc123",
            ],
        ):
            args = parse_args()
        self.assertEqual(args.artifacts_dir, "/tmp/artifacts")
        self.assertEqual(args.output_dir, "/tmp/output")
        self.assertEqual(args.repo, "owner/repo")
        self.assertEqual(args.sha, "abc123")
        self.assertFalse(args.enable_llm_patches)
        self.assertEqual(args.max_llm_patches, 10)

    def test_all_args(self) -> None:
        from scripts.quality.rollup_v2.__main__ import parse_args

        with patch(
            "sys.argv",
            [
                "__main__.py",
                "--artifacts-dir", "/tmp/a",
                "--output-dir", "/tmp/o",
                "--repo", "owner/repo",
                "--sha", "def456",
                "--enable-llm-patches",
                "--max-llm-patches", "5",
            ],
        ):
            args = parse_args()
        self.assertTrue(args.enable_llm_patches)
        self.assertEqual(args.max_llm_patches, 5)

    def test_help_text(self) -> None:
        from scripts.quality.rollup_v2.__main__ import parse_args

        with patch("sys.argv", ["__main__.py", "--help"]):
            with self.assertRaises(SystemExit) as ctx:
                parse_args()
            self.assertEqual(ctx.exception.code, 0)


class MainFunctionTests(unittest.TestCase):
    """Test main() function integration."""

    def test_main_returns_zero_on_success(self) -> None:
        from scripts.quality.rollup_v2.__main__ import main

        with tempfile.TemporaryDirectory() as tmp:
            artifacts_dir = Path(tmp) / "artifacts"
            artifacts_dir.mkdir()
            output_dir = Path(tmp) / "output"
            output_dir.mkdir()

            with patch(
                "sys.argv",
                [
                    "__main__.py",
                    "--artifacts-dir", str(artifacts_dir),
                    "--output-dir", str(output_dir),
                    "--repo", "owner/repo",
                    "--sha", "abc123",
                ],
            ):
                result = main()
        self.assertEqual(result, 0)

    def test_main_loads_json_artifacts(self) -> None:
        from scripts.quality.rollup_v2.__main__ import main

        with tempfile.TemporaryDirectory() as tmp:
            artifacts_dir = Path(tmp) / "artifacts"
            artifacts_dir.mkdir()
            output_dir = Path(tmp) / "output"
            output_dir.mkdir()

            # Put a qlty artifact in the expected location
            qlty_dir = artifacts_dir / "qlty"
            qlty_dir.mkdir()
            (qlty_dir / "qlty.json").write_text(
                '{"issues": []}', encoding="utf-8"
            )

            with patch(
                "sys.argv",
                [
                    "__main__.py",
                    "--artifacts-dir", str(artifacts_dir),
                    "--output-dir", str(output_dir),
                    "--repo", "owner/repo",
                    "--sha", "abc123",
                ],
            ):
                result = main()
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
