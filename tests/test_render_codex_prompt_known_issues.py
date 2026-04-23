"""Tests for the known-issues section in ``render_codex_prompt``.

Phase 4 of ``docs/QZP-V2-DESIGN.md`` §6 requires QRv2 to read the
``known-issues/`` registry into its Codex prompt so the remediation
loop applies the canonical fix for each documented false positive.
These tests pin that contract.
"""

from __future__ import absolute_import

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.quality import render_codex_prompt as rcp


class KnownIssuesSectionTests(unittest.TestCase):
    """``_render_known_issues_section`` emits a prompt block from the registry."""

    def _write_registry(self, files: dict) -> Path:
        """Create a temp registry dir seeded with ``files``."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: None)  # TemporaryDirectory not used on purpose
        for name, body in files.items():
            (root / name).write_text(body, encoding="utf-8")
        return root

    def test_empty_registry_returns_empty_string(self) -> None:
        """Missing / empty registry means no section emitted — no dangling heading."""
        empty = self._write_registry({})
        self.assertEqual(rcp._render_known_issues_section(empty), "")

    def test_registry_without_qrv2_feeding_entries_is_empty(self) -> None:
        """Entries with ``feeds_qrv2: false`` don't populate the section."""
        reg = self._write_registry({
            "QZ-FP-OFF.yml": (
                "id: QZ-FP-OFF\ntitle: off\ndescription: off\n"
                "affects: [x]\nfeeds_qrv2: false\nverified_at: '2026-04-23'\n"
            ),
        })
        self.assertEqual(rcp._render_known_issues_section(reg), "")

    def test_shipped_registry_produces_section(self) -> None:
        """The real ``known-issues/`` registry renders all 4 entries."""
        section = rcp._render_known_issues_section()
        self.assertIn("## Known-issues registry", section)
        self.assertIn("QZ-FP-001", section)
        self.assertIn("QZ-FP-002", section)
        self.assertIn("QZ-FP-003", section)
        self.assertIn("QZ-CV-001", section)

    def test_canonical_fix_rendered_in_code_fence(self) -> None:
        """Each entry's ``fix_snippet`` lands inside a fenced block."""
        reg = self._write_registry({
            "QZ-OK-1.yml": (
                "id: QZ-OK-1\ntitle: title\ndescription: why\n"
                "affects: [codeql]\nfeeds_qrv2: true\n"
                "fix_snippet: 'pass # fix'\nverified_at: '2026-04-23'\n"
            ),
        })
        section = rcp._render_known_issues_section(reg)
        self.assertIn("Canonical fix:", section)
        self.assertIn("```\npass # fix\n```", section)

    def test_affects_list_formatted(self) -> None:
        """``affects`` renders as a comma-joined list."""
        reg = self._write_registry({
            "QZ-OK-2.yml": (
                "id: QZ-OK-2\ntitle: t\ndescription: d\n"
                "affects: [codeql, sonarcloud]\nfeeds_qrv2: true\n"
                "fix_snippet: 'pass'\nverified_at: '2026-04-23'\n"
            ),
        })
        section = rcp._render_known_issues_section(reg)
        self.assertIn("Affects: codeql, sonarcloud", section)


class PromptIntegrationTests(unittest.TestCase):
    """``_render_prompt`` splices the known-issues section onto the prompt."""

    _MIN_PROFILE = {
        "slug": "Prekzursil/example",
        "verify_command": "bash scripts/verify",
        "default_branch": "main",
        "profile_id": "example",
        "stack": "python-only",
        "github_mutation_lane": "default",
        "codex_auth_lane": "default",
        "provider_ui_mode": "default",
        "codex_environment": {
            "auth_file": "~/.codex/auth.json",
            "runner_labels": ["self-hosted"],
        },
        "required_contexts": {
            "always": ["codeql / CodeQL"],
            "pull_request_only": [],
            "required_now": ["codeql / CodeQL"],
            "target": ["codeql / CodeQL"],
        },
        "required_secrets": [],
        "conditional_secrets": [],
        "required_vars": [],
        "issue_policy": {"mode": "ratchet"},
        "enabled_scanners": {},
        "coverage": {},
        "vendors": {},
        "preserve_public_check_names": True,
    }

    def test_known_issues_section_appended_when_entries_exist(self) -> None:
        """The shipped registry populates the section on every render."""
        prompt = rcp._render_prompt(
            self._MIN_PROFILE,
            lane="remediation",
            event_name="pull_request",
            failure_context="",
            artifacts=[],
        )
        self.assertIn("## Known-issues registry", prompt)
        self.assertIn("QZ-FP-001", prompt)

    def test_known_issues_section_omitted_when_registry_empty(self) -> None:
        """Empty registry → prompt has no dangling heading."""
        with patch.object(
            rcp, "_render_known_issues_section", return_value=""
        ):
            prompt = rcp._render_prompt(
                self._MIN_PROFILE,
                lane="remediation",
                event_name="pull_request",
                failure_context="",
                artifacts=[],
            )
        self.assertNotIn("## Known-issues registry", prompt)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
