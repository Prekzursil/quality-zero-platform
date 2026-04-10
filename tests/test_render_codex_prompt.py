"""Test render codex prompt."""

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
from scripts.quality.render_codex_prompt import (
    _parse_args,
    _render_canonical_findings_section,
    _render_prompt,
)


class RenderCodexPromptTests(unittest.TestCase):
    """Render Codex Prompt Tests."""

    def test_parse_args_supports_expected_defaults(self) -> None:
        """Cover parse args supports expected defaults."""
        with patch.object(
            sys,
            "argv",
            [
                "render_codex_prompt.py",
                "--repo-slug",
                "Prekzursil/quality-zero-platform",
            ],
        ):
            args = _parse_args()

        self.assertEqual(args.lane, "remediation")
        self.assertEqual(args.event_name, "pull_request")
        self.assertEqual(args.failure_context, "")
        self.assertEqual(args.artifact, [])

    def test_render_prompt_preserves_contract_sections_and_artifacts(self) -> None:
        """Cover render prompt preserves contract sections and artifacts."""
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

    def test_render_prompt_rejects_non_mapping_profiles_and_unexpected_kwargs(
        self,
    ) -> None:
        """Cover render prompt rejects non mapping profiles and unexpected kwargs."""
        with self.assertRaises(TypeError):
            _render_prompt(
                lane="remediation",
                event_name="pull_request",
                failure_context="",
                artifacts=[],
            )

        with self.assertRaises(TypeError):
            _render_prompt(
                {
                    "slug": "x",
                    "verify_command": "y",
                    "default_branch": "main",
                    "preserve_public_check_names": True,
                },
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
                {
                    "slug": "x",
                    "verify_command": "y",
                    "default_branch": "main",
                    "preserve_public_check_names": True,
                },
                lane="remediation",
                event_name="pull_request",
                failure_context="",
                artifacts=[],
                extra=True,
            )

    def test_main_prints_or_writes_the_rendered_prompt(self) -> None:
        """Cover main prints or writes the rendered prompt."""
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
                "canonical_json": "",
                "output": "",
            },
        )()

        stdout = io.StringIO()
        with patch.object(
            render_codex_prompt, "_parse_args", return_value=args
        ), patch.object(
            render_codex_prompt, "load_inventory", return_value={"repos": []}
        ), patch.object(
            render_codex_prompt,
            "load_repo_profile",
            return_value={"slug": "Prekzursil/quality-zero-platform"},
        ), patch.object(
            render_codex_prompt, "_render_prompt", return_value=prompt_text
        ), patch(
            "sys.stdout",
            stdout,
        ):
            self.assertEqual(render_codex_prompt.main(), 0)

        self.assertEqual(stdout.getvalue(), prompt_text + "\n")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "prompt.md"
            args.output = str(output_path)
            with patch.object(
                render_codex_prompt, "_parse_args", return_value=args
            ), patch.object(
                render_codex_prompt, "load_inventory", return_value={"repos": []}
            ), patch.object(
                render_codex_prompt,
                "load_repo_profile",
                return_value={"slug": "Prekzursil/quality-zero-platform"},
            ), patch.object(
                render_codex_prompt, "_render_prompt", return_value=prompt_text
            ):
                self.assertEqual(render_codex_prompt.main(), 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), prompt_text)

    def test_script_entrypoint_reinserts_repo_root_when_missing(self) -> None:
        """Cover script entrypoint reinserts repo root when missing."""
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
        ), patch(
            "sys.stdout", buffer
        ):
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

    def test_parse_args_accepts_canonical_json(self) -> None:
        """Cover --canonical-json argument is accepted."""
        with patch.object(
            sys,
            "argv",
            [
                "render_codex_prompt.py",
                "--repo-slug",
                "owner/repo",
                "--canonical-json",
                "/tmp/canonical.json",
            ],
        ):
            args = _parse_args()
        self.assertEqual(args.canonical_json, "/tmp/canonical.json")

    def test_parse_args_canonical_json_defaults_empty(self) -> None:
        """Cover --canonical-json defaults to empty string."""
        with patch.object(
            sys,
            "argv",
            ["render_codex_prompt.py", "--repo-slug", "owner/repo"],
        ):
            args = _parse_args()
        self.assertEqual(args.canonical_json, "")


class RenderCanonicalFindingsSectionTests(unittest.TestCase):
    """Tests for the canonical findings section renderer."""

    def _make_finding(
        self,
        *,
        finding_id: str = "f1",
        file: str = "example.py",
        line: int = 42,
        category: str = "broad-except",
        severity: str = "high",
        primary_message: str = "Catch a more specific exception",
        fix_hint: str | None = "Narrow the exception type",
        patch_diff: str | None = None,
        patch_source: str = "none",
        corroborators: list | None = None,
    ) -> dict:
        return {
            "finding_id": finding_id,
            "file": file,
            "line": line,
            "category": category,
            "severity": severity,
            "primary_message": primary_message,
            "fix_hint": fix_hint,
            "patch": patch_diff,
            "patch_source": patch_source,
            "corroborators": corroborators or [],
        }

    def test_renders_finding_with_all_fields(self) -> None:
        """A finding with fix_hint, corroborators, and patch renders fully."""
        findings = [
            self._make_finding(
                corroborators=[
                    {"provider": "Codacy"},
                    {"provider": "SonarCloud"},
                    {"provider": "DeepSource"},
                ],
                patch_diff="--- a/example.py\n+++ b/example.py\n@@ -1 +1 @@\n-old\n+new\n",
                patch_source="llm",
            ),
        ]
        section = _render_canonical_findings_section(findings)
        self.assertIn("### Finding: broad-except", section)
        self.assertIn("line 42", section)
        self.assertIn("example.py", section)
        self.assertIn("**Severity**: high", section)
        self.assertIn("Catch a more specific exception", section)
        self.assertIn("Narrow the exception type", section)
        self.assertIn("Codacy", section)
        self.assertIn("SonarCloud", section)
        self.assertIn("DeepSource", section)
        self.assertIn("```diff", section)
        self.assertIn("-old", section)
        self.assertIn("+new", section)

    def test_renders_finding_without_optional_fields(self) -> None:
        """A finding without fix_hint or patch omits those sections."""
        findings = [
            self._make_finding(fix_hint=None, patch_diff=None),
        ]
        section = _render_canonical_findings_section(findings)
        self.assertIn("### Finding: broad-except", section)
        self.assertNotIn("Fix hint", section)
        self.assertNotIn("```diff", section)

    def test_empty_findings_returns_empty_string(self) -> None:
        """No findings produces empty section."""
        self.assertEqual(_render_canonical_findings_section([]), "")

    def test_multiple_findings_grouped_by_file(self) -> None:
        """Multiple findings in the same file appear together."""
        findings = [
            self._make_finding(finding_id="f1", file="a.py", line=10),
            self._make_finding(finding_id="f2", file="a.py", line=20, category="unused-import"),
            self._make_finding(finding_id="f3", file="b.py", line=5, category="dead-code"),
        ]
        section = _render_canonical_findings_section(findings)
        self.assertIn("### File: a.py", section)
        self.assertIn("### File: b.py", section)
        # Both findings for a.py should appear
        self.assertIn("broad-except", section)
        self.assertIn("unused-import", section)
        self.assertIn("dead-code", section)
