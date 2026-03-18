from __future__ import absolute_import

import io
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

from scripts.quality import render_codex_prompt
from scripts.quality.render_codex_prompt import _parse_args, _render_prompt


class RenderCodexPromptTests(unittest.TestCase):
    def test_parse_args_supports_expected_defaults(self) -> None:
        with patch.object(
            sys,
            "argv",
            ["render_codex_prompt.py", "--repo-slug", "Prekzursil/quality-zero-platform"],
        ):
            args = _parse_args()

        self.assertEqual(args.lane, "remediation")
        self.assertEqual(args.event_name, "pull_request")
        self.assertEqual(args.failure_context, "")
        self.assertEqual(args.artifact, [])

    def test_render_prompt_preserves_contract_sections_and_artifacts(self) -> None:
        profile = {
            "slug": "Prekzursil/quality-zero-platform",
            "verify_command": "bash scripts/verify",
            "default_branch": "main",
            "preserve_public_check_names": True,
            "codex_environment": {
                "mode": "automatic",
                "verify_command": "bash scripts/verify",
                "auth_file": "~/.codex/auth.json",
                "network_profile": "unrestricted",
                "methods": "all",
                "runner_labels": ["self-hosted", "codex-trusted"],
            },
        }

        with patch(
            "scripts.quality.render_codex_prompt.active_required_contexts",
            return_value=["Coverage 100 Gate", "QLTY Zero"],
        ):
            prompt = _render_prompt(
                profile,
                lane="remediation",
                event_name="pull_request",
                failure_context="failed quality gate",
                artifacts=["artifact-a", "artifact-b"],
            )

        self.assertTrue(prompt.startswith("# Codex PR failure remediation\n"))
        self.assertIn("Repo: Prekzursil/quality-zero-platform", prompt)
        self.assertIn("Lane: remediation", prompt)
        self.assertIn("Failure context: failed quality gate", prompt)
        self.assertIn("- Verify command: `bash scripts/verify`", prompt)
        self.assertIn("- Codex runner labels: `self-hosted, codex-trusted`", prompt)
        self.assertIn("## Required contexts for pull_request", prompt)
        self.assertIn("- `Coverage 100 Gate`", prompt)
        self.assertIn("- `QLTY Zero`", prompt)
        self.assertIn("## Artifacts", prompt)
        self.assertIn("- artifact-a", prompt)
        self.assertIn("- artifact-b", prompt)
        self.assertTrue(prompt.endswith("\n"))

    def test_render_prompt_rejects_non_mapping_profiles_and_unexpected_kwargs(self) -> None:
        with self.assertRaises(TypeError):
            _render_prompt(
                lane="remediation",
                event_name="pull_request",
                failure_context="",
                artifacts=[],
            )

        with self.assertRaises(TypeError):
            _render_prompt(
                {"slug": "x", "verify_command": "y", "default_branch": "main", "preserve_public_check_names": True},
                lane="remediation",
                event_name="pull_request",
                failure_context="",
                artifacts=object(),
            )

        with self.assertRaises(TypeError):
            _render_prompt(
                ["not", "a", "mapping"],
                lane="remediation",
                event_name="pull_request",
                failure_context="",
                artifacts=[],
            )

        with self.assertRaises(TypeError):
            _render_prompt(
                {"slug": "x", "verify_command": "y", "default_branch": "main", "preserve_public_check_names": True},
                lane="remediation",
                event_name="pull_request",
                failure_context="",
                artifacts=[],
                extra=True,
            )

    def test_main_prints_or_writes_the_rendered_prompt(self) -> None:
        prompt_text = "# Codex PR failure remediation\n"
        args = type(
            "Args",
            (),
            {
                "inventory": "",
                "repo_slug": "Prekzursil/quality-zero-platform",
                "lane": "remediation",
                "event_name": "pull_request",
                "failure_context": "",
                "artifact": [],
                "output": "",
            },
        )()

        stdout = io.StringIO()
        with patch.object(render_codex_prompt, "_parse_args", return_value=args), patch.object(
            render_codex_prompt, "load_inventory", return_value={"repos": []}
        ), patch.object(
            render_codex_prompt, "load_repo_profile", return_value={"slug": "Prekzursil/quality-zero-platform"}
        ), patch.object(render_codex_prompt, "_render_prompt", return_value=prompt_text), patch(
            "sys.stdout",
            stdout,
        ):
            self.assertEqual(render_codex_prompt.main(), 0)

        self.assertEqual(stdout.getvalue(), prompt_text + "\n")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "prompt.md"
            args.output = str(output_path)
            with patch.object(render_codex_prompt, "_parse_args", return_value=args), patch.object(
                render_codex_prompt, "load_inventory", return_value={"repos": []}
            ), patch.object(
                render_codex_prompt, "load_repo_profile", return_value={"slug": "Prekzursil/quality-zero-platform"}
            ), patch.object(render_codex_prompt, "_render_prompt", return_value=prompt_text):
                self.assertEqual(render_codex_prompt.main(), 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), prompt_text)

    def test_script_entrypoint_reinserts_repo_root_when_missing(self) -> None:
        script_path = Path("scripts/quality/render_codex_prompt.py").resolve()
        root_text = str(Path.cwd().resolve())
        trimmed_sys_path = [item for item in sys.path if item != root_text]
        fake_inventory: Dict[str, Any] = {"repos": []}
        fake_profile = {
            "slug": "Prekzursil/quality-zero-platform",
            "verify_command": "bash scripts/verify",
            "default_branch": "main",
            "preserve_public_check_names": True,
            "codex_environment": {},
        }
        buffer = io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            sys,
            "argv",
            [str(script_path), "--repo-slug", "Prekzursil/quality-zero-platform"],
        ), patch.object(sys, "path", trimmed_sys_path[:]), patch(
            "scripts.quality.render_codex_prompt.load_inventory",
            return_value=fake_inventory,
        ), patch(
            "scripts.quality.render_codex_prompt.load_repo_profile",
            return_value=fake_profile,
        ), patch(
            "scripts.quality.render_codex_prompt.active_required_contexts",
            return_value=["Coverage 100 Gate"],
        ), patch("sys.stdout", buffer):
            cwd = Path(tmpdir)
            previous = Path.cwd()
            try:
                os.chdir(cwd)
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(script_path), run_name="__main__")
            finally:
                os.chdir(previous)

        self.assertEqual(result.exception.code, 0)
        self.assertIn("Coverage 100 Gate", buffer.getvalue())
