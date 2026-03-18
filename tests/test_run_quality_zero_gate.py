from __future__ import absolute_import

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.quality.run_quality_zero_gate import _build_argv, _parse_args, main


class RunQualityZeroGateTests(unittest.TestCase):
    def test_parse_args_supports_expected_defaults(self) -> None:
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

    def test_build_argv_uses_profile_contexts_and_explicit_outputs(self) -> None:
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

    def test_main_invokes_required_checks_probe_from_profile_json(self) -> None:
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

            completed = SimpleNamespace(returncode=0)
            with patch("scripts.quality.run_quality_zero_gate._parse_args", return_value=args), patch(
                "scripts.quality.run_quality_zero_gate.subprocess.run", return_value=completed
            ) as mock_run, patch.dict("os.environ", {"TARGET_SHA": "deadbeef"}, clear=False):
                exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            mock_run.call_args.args[0],
            [
                str(Path(args.platform_dir).resolve() / "scripts" / "quality" / "check_required_checks.py"),
                "--repo",
                "Prekzursil/quality-zero-platform",
                "--sha",
                "deadbeef",
                "--out-json",
                "quality-zero-gate/required-checks.json",
                "--out-md",
                "quality-zero-gate/required-checks.md",
                "--required-context",
                "Coverage 100 Gate",
            ],
        )
        self.assertEqual(mock_run.call_args.kwargs["cwd"], Path(args.repo_dir).resolve())
