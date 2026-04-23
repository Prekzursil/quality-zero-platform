"""Coverage for ``_coverage_inputs_payload`` + the new ``coverage_inputs_json``.

Phase 2 of docs/QZP-V2-DESIGN.md wires reusable-codecov-analytics.yml to
loop once per coverage input. Emitting the structured payload from the
profile exporter is the first step; these tests lock in the shape so the
workflow can rely on it.
"""

from __future__ import absolute_import

import json
import tempfile
import unittest
from pathlib import Path

from scripts.quality.export_profile import (
    _coverage_inputs_payload,
    _profile_output_lines,
)


class CoverageInputsPayloadTests(unittest.TestCase):
    """Shape-only tests for the per-input payload helper."""

    def test_full_input_preserved(self) -> None:
        """All four fields carry through when present."""
        payload = _coverage_inputs_payload(
            {
                "inputs": [
                    {
                        "name": "backend",
                        "flag": "backend",
                        "path": "backend/coverage.xml",
                        "format": "xml",
                    }
                ]
            }
        )
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["path"], "backend/coverage.xml")
        self.assertEqual(payload[0]["flag"], "backend")
        self.assertEqual(payload[0]["name"], "backend")
        self.assertEqual(payload[0]["format"], "xml")

    def test_flag_falls_back_to_name_when_missing(self) -> None:
        """A profile omitting ``flag`` reuses ``name`` so the upload is keyed."""
        payload = _coverage_inputs_payload(
            {"inputs": [{"name": "legacy", "path": "build/cov.xml"}]}
        )
        self.assertEqual(payload, [
            {"path": "build/cov.xml", "flag": "legacy", "name": "legacy", "format": ""},
        ])

    def test_windows_backslashes_normalised_to_forward(self) -> None:
        """Windows-style paths are normalised so Codecov sees POSIX form."""
        payload = _coverage_inputs_payload(
            {"inputs": [
                {"name": "win", "flag": "win", "path": r"a\b\c.xml"},
            ]}
        )
        self.assertEqual(payload[0]["path"], "a/b/c.xml")

    def test_missing_path_is_dropped(self) -> None:
        """Inputs without a ``path`` are skipped rather than uploaded as empty."""
        payload = _coverage_inputs_payload(
            {"inputs": [{"name": "orphan"}]}
        )
        self.assertEqual(payload, [])

    def test_empty_or_missing_coverage_returns_empty(self) -> None:
        """No ``inputs`` key ⇒ empty list (not an error)."""
        self.assertEqual(_coverage_inputs_payload({}), [])
        self.assertEqual(_coverage_inputs_payload({"inputs": []}), [])


class ProfileOutputLinesCoverageInputsTests(unittest.TestCase):
    """The exported ``coverage_inputs_json`` output key reflects the payload."""

    def _minimum_profile(self) -> dict:
        """A profile skeleton with just enough to pass ``_profile_output_lines``."""
        return {
            "slug": "Prekzursil/example",
            "verify_command": "bash scripts/verify",
            "default_branch": "main",
            "profile_id": "example",
            "stack": "fullstack-web",
            "github_mutation_lane": "default",
            "codex_auth_lane": "default",
            "provider_ui_mode": "default",
            "codex_environment": {
                "auth_file": "~/.codex/auth.json",
                "runner_labels": ["self-hosted"],
            },
            "required_contexts": {
                "always": [],
                "pull_request_only": [],
                "required_now": [],
                "target": [],
            },
            "required_secrets": [],
            "conditional_secrets": [],
            "required_vars": [],
            "issue_policy": {"mode": "ratchet"},
            "enabled_scanners": {"codecov": True},
            "coverage": {
                "inputs": [
                    {
                        "name": "backend",
                        "flag": "backend",
                        "path": "backend/coverage.xml",
                        "format": "xml",
                    },
                    {
                        "name": "frontend",
                        "flag": "ui",
                        "path": "ui/coverage/lcov.info",
                        "format": "lcov",
                    },
                ]
            },
            "vendors": {},
        }

    def test_coverage_inputs_json_line_matches_payload(self) -> None:
        """The ``coverage_inputs_json`` line carries both inputs with flags."""
        profile = self._minimum_profile()
        lines = _profile_output_lines(profile, event_name="pull_request")
        matches = [ln for ln in lines if ln.startswith("coverage_inputs_json=")]
        self.assertEqual(len(matches), 1)
        payload = json.loads(matches[0].split("=", 1)[1])
        self.assertEqual(len(payload), 2)
        self.assertEqual(
            {p["flag"] for p in payload},
            {"backend", "ui"},
        )

    def test_legacy_coverage_input_files_still_emitted(self) -> None:
        """Backward-compat: legacy comma-joined string stays available."""
        profile = self._minimum_profile()
        lines = _profile_output_lines(profile, event_name="pull_request")
        matches = [ln for ln in lines if ln.startswith("coverage_input_files=")]
        self.assertEqual(len(matches), 1)
        self.assertIn("backend/coverage.xml", matches[0])
        self.assertIn("ui/coverage/lcov.info", matches[0])


class GithubActionsOutputFileTests(unittest.TestCase):
    """Full end-to-end: profile → GitHub Actions output file format."""

    def test_github_output_contains_coverage_inputs_json(self) -> None:
        """Running the CLI mode writes the new key to GITHUB_OUTPUT."""
        from scripts.quality.export_profile import _write_github_output

        profile = ProfileOutputLinesCoverageInputsTests()._minimum_profile()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "github_output.txt"
            _write_github_output(out, profile, event_name="pull_request")
            content = out.read_text(encoding="utf-8")
            self.assertIn("coverage_inputs_json=", content)
            # And the payload itself must be valid JSON.
            line = next(
                ln for ln in content.splitlines()
                if ln.startswith("coverage_inputs_json=")
            )
            payload = json.loads(line.split("=", 1)[1])
            self.assertIsInstance(payload, list)
            self.assertEqual(len(payload), 2)


if __name__ == "__main__":
    unittest.main()
