"""Test run quality zero gate."""

from __future__ import absolute_import

import json
import importlib
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
import runpy
import sys
from typing import List
from unittest.mock import patch

from scripts.quality.run_quality_zero_gate import (
    _build_argv,
    _parse_args,
    _run_required_checks,
    main,
)


class RunQualityZeroGateTests(unittest.TestCase):
    """Run Quality Zero Gate Tests."""

    def test_parse_args_supports_expected_defaults(self) -> None:
        """Cover parse args supports expected defaults."""
        with patch(
            "sys.argv",
            [
                "run_quality_zero_gate.py",
                "--profile-json",
                "profile.json",
            ],
        ):
            args = _parse_args()

        self.assertEqual(args.profile_json, "profile.json")
        self.assertEqual(args.repo_dir, ".")
        self.assertEqual(args.platform_dir, "")
        self.assertEqual(args.out_json, "quality-zero-gate/required-checks.json")
        self.assertEqual(args.out_md, "quality-zero-gate/required-checks.md")

    def test_required_contexts_uses_fallback_target_and_rejects_invalid_profiles(
        self,
    ) -> None:
        """Cover required contexts uses fallback target and rejects invalid profiles."""
        self.assertEqual(
            importlib.import_module(
                "scripts.quality.run_quality_zero_gate"
            )._required_contexts(
                {
                    "required_contexts": {
                        "target": [" Coverage 100 Gate ", "", "qlty check"]
                    }
                }
            ),
            ["Coverage 100 Gate", "qlty check"],
        )

        with self.assertRaises(SystemExit):
            importlib.import_module(
                "scripts.quality.run_quality_zero_gate"
            )._required_contexts({"required_contexts": "not-a-dict"})

    def test_build_argv_uses_profile_contexts_and_explicit_outputs(self) -> None:
        """Cover build argv uses profile contexts and explicit outputs."""
        profile = {
            "slug": "Prekzursil/quality-zero-platform",
            "active_required_contexts": ["Coverage 100 Gate", "qlty check"],
        }

        argv = _build_argv(
            profile,
            "abc123",
            platform_dir=Path(r"C:\repo\platform"),
            out_json="out.json",
            out_md="out.md",
        )

        self.assertEqual(
            argv,
            [
                r"C:\repo\platform\scripts\quality\check_required_checks.py",
                "--repo",
                "Prekzursil/quality-zero-platform",
                "--sha",
                "abc123",
                "--out-json",
                "out.json",
                "--out-md",
                "out.md",
                "--required-context",
                "Coverage 100 Gate",
                "--required-context",
                "qlty check",
            ],
        )

    def test_build_argv_supports_positional_args_and_rejects_invalid_calls(
        self,
    ) -> None:
        """Cover build argv supports positional args and rejects invalid calls."""
        profile = {
            "slug": "Prekzursil/quality-zero-platform",
            "active_required_contexts": ["Coverage 100 Gate"],
        }

        argv = _build_argv(
            profile,
            "abc123",
            Path(r"C:\repo\platform"),
            "out.json",
            "out.md",
        )

        self.assertEqual(
            argv,
            [
                r"C:\repo\platform\scripts\quality\check_required_checks.py",
                "--repo",
                "Prekzursil/quality-zero-platform",
                "--sha",
                "abc123",
                "--out-json",
                "out.json",
                "--out-md",
                "out.md",
                "--required-context",
                "Coverage 100 Gate",
            ],
        )

        with self.assertRaises(TypeError):
            _build_argv(profile, "abc123", Path(r"C:\repo\platform"), "out.json")

        with self.assertRaises(TypeError):
            _build_argv(
                profile,
                "abc123",
                platform_dir=Path(r"C:\repo\platform"),
                out_json="out.json",
                out_md="out.md",
                extra="boom",
            )

    def test_main_invokes_required_checks_probe_from_profile_json(self) -> None:
        """Cover main invokes required checks probe from profile json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            profile_path = tmp / "profile.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "slug": "Prekzursil/quality-zero-platform",
                        "active_required_contexts": ["Coverage 100 Gate"],
                    }
                ),
                encoding="utf-8",
            )
            args = Namespace(
                profile_json=str(profile_path),
                repo_dir=str(tmp / "repo"),
                platform_dir=str(tmp / "platform"),
                out_json="quality-zero-gate/required-checks.json",
                out_md="quality-zero-gate/required-checks.md",
            )
            (tmp / "repo").mkdir()
            (tmp / "platform" / "scripts" / "quality").mkdir(parents=True)

            with (
                patch(
                    "scripts.quality.run_quality_zero_gate._parse_args",
                    return_value=args,
                ),
                patch(
                    "scripts.quality.run_quality_zero_gate.check_required_checks.main",
                    return_value=0,
                ) as mock_main,
                patch.dict("os.environ", {"TARGET_SHA": "deadbeef"}, clear=False),
            ):
                exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_main.call_count, 1)

    def test_main_requires_target_sha_or_github_sha(self) -> None:
        """Cover main requires target sha or github sha."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            profile_path = tmp / "profile.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "slug": "Prekzursil/quality-zero-platform",
                        "active_required_contexts": ["Coverage 100 Gate"],
                    }
                ),
                encoding="utf-8",
            )
            args = Namespace(
                profile_json=str(profile_path),
                repo_dir=str(tmp / "repo"),
                platform_dir=str(tmp / "platform"),
                out_json="quality-zero-gate/required-checks.json",
                out_md="quality-zero-gate/required-checks.md",
            )

            with (
                patch(
                    "scripts.quality.run_quality_zero_gate._parse_args",
                    return_value=args,
                ),
                patch.dict("os.environ", {}, clear=True),
            ):
                with self.assertRaises(SystemExit) as exc:
                    main()

        self.assertEqual(str(exc.exception), "TARGET_SHA or GITHUB_SHA is required")

    def test_module_execution_as_main_inserts_repo_root_and_exits_cleanly(self) -> None:
        """Cover module execution as main inserts repo root and exits cleanly."""
        module_path = (
            importlib.import_module("scripts.quality.run_quality_zero_gate").__file__
            or ""
        )
        self.assertTrue(module_path)
        repo_root = str(Path(module_path).resolve().parents[2])
        original_sys_path = list(sys.path)
        try:
            while repo_root in sys.path:
                sys.path.remove(repo_root)
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                profile_path = tmp / "profile.json"
                profile_path.write_text(
                    json.dumps(
                        {
                            "slug": "Prekzursil/quality-zero-platform",
                            "active_required_contexts": ["Coverage 100 Gate"],
                        }
                    ),
                    encoding="utf-8",
                )
                (tmp / "repo").mkdir()
                (tmp / "platform" / "scripts" / "quality").mkdir(parents=True)

                with (
                    patch("scripts.quality.check_required_checks.main", return_value=0),
                    patch.object(
                        sys,
                        "argv",
                        [
                            "run_quality_zero_gate.py",
                            "--profile-json",
                            str(profile_path),
                            "--repo-dir",
                            str(tmp / "repo"),
                            "--platform-dir",
                            str(tmp / "platform"),
                        ],
                    ),
                    patch.dict("os.environ", {"TARGET_SHA": "deadbeef"}, clear=False),
                    self.assertRaises(SystemExit) as exc,
                ):
                    runpy.run_module(
                        "scripts.quality.run_quality_zero_gate", run_name="__main__"
                    )
            self.assertEqual(exc.exception.code, 0)
            self.assertIn(repo_root, sys.path)
        finally:
            sys.path[:] = original_sys_path

    def test_run_required_checks_uses_repo_dir_as_working_directory_and_restores_argv(
        self,
    ) -> None:
        """Cover run required checks uses repo dir as working directory and restores argv."""
        original_argv = list(sys.argv)
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir) / "repo"
            repo_dir.mkdir()
            argv = [
                str(
                    Path(tmpdir)
                    / "platform"
                    / "scripts"
                    / "quality"
                    / "check_required_checks.py"
                ),
                "--repo",
                "Prekzursil/quality-zero-platform",
                "--sha",
                "deadbeef",
            ]
            cwd_during_call: List[Path] = []
            argv_during_call: List[List[str]] = []

            def _fake_main() -> int:
                """Handle fake main."""
                cwd_during_call.append(Path.cwd())
                argv_during_call.append(list(sys.argv))
                return 0

            with patch(
                "scripts.quality.run_quality_zero_gate.check_required_checks.main",
                side_effect=_fake_main,
            ):
                exit_code = _run_required_checks(argv, repo_dir=repo_dir)

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(cwd_during_call), 1)
        self.assertEqual(cwd_during_call[0].parts[-2:], repo_dir.resolve().parts[-2:])
        self.assertEqual(argv_during_call, [argv])
        self.assertEqual(sys.argv, original_argv)
