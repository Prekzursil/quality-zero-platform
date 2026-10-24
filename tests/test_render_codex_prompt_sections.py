"""Tests for the optional parts of the Codex prompt (``scripts/quality/render_codex_prompt.py``).

* a known-issues entry renders only the fields it actually has;
* ``main`` appends the canonical-findings section from ``--canonical-json``
  only when that file exists and names findings.
"""

from __future__ import absolute_import

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.quality import render_codex_prompt as rcp

BASE_PROMPT = "# Codex PR failure remediation\n"


class KnownIssueEntryFieldsTests(unittest.TestCase):
    """Missing optional fields leave no empty headings behind."""

    def test_an_entry_with_only_an_id_and_title_renders_just_its_heading(self) -> None:
        """No description, no affects, a blank fix: heading only, then the next entry."""
        entries = [
            {"id": "QZ-FP-901", "title": "bare", "description": "  ", "affects": [], "fix_snippet": "   "},
            {"id": "QZ-FP-902", "title": "full", "description": "Why.", "affects": ["a.py"], "fix_snippet": "x = 1"},
        ]
        with (
            patch.object(rcp, "load_known_issues", return_value=[]),
            patch.object(rcp, "qrv2_prompt_entries", return_value=entries),
        ):
            section = rcp._render_known_issues_section(Path("unused"))
        bare = section.split("### QZ-FP-901 — bare\n", 1)[1].split("### QZ-FP-902", 1)[0]
        self.assertEqual(bare.strip(), "")
        self.assertIn("Why.\n\nAffects: a.py\n\nCanonical fix:\n\n```\nx = 1\n```", section)


class MainCanonicalFindingsTests(unittest.TestCase):
    """`--canonical-json` adds the findings section only when there is something to add."""

    def setUp(self) -> None:
        """A scratch directory for the canonical JSON."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _main(self, canonical_json: str) -> str:
        args = SimpleNamespace(
            inventory="",
            repo_slug="Prekzursil/quality-zero-platform",
            lane="remediation",
            event_name="pull_request",
            failure_context="",
            artifact=[],
            canonical_json=canonical_json,
            output="",
        )
        stdout = io.StringIO()
        with (
            patch.object(rcp, "_parse_args", return_value=args),
            patch.object(rcp, "load_inventory", return_value={"repos": []}),
            patch.object(rcp, "load_repo_profile", return_value={"slug": args.repo_slug}),
            patch.object(rcp, "_render_prompt", return_value=BASE_PROMPT),
            patch("sys.stdout", stdout),
        ):
            self.assertEqual(rcp.main(), 0)
        return stdout.getvalue()

    def _write(self, document: object) -> str:
        path = self.tmp / "canonical.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return str(path)

    def test_findings_are_appended_after_the_prompt(self) -> None:
        """A canonical finding becomes its own section after the base prompt."""
        finding = {
            "finding_id": "f1",
            "file": "example.py",
            "line": 7,
            "category": "broad-except",
            "severity": "high",
            "primary_message": "Catch a narrower exception",
            "fix_hint": None,
            "patch": None,
            "patch_source": "none",
            "corroborators": [],
        }
        printed = self._main(self._write({"findings": [finding]}))
        self.assertTrue(printed.startswith(BASE_PROMPT.rstrip("\n") + "\n\n"))
        self.assertIn("### Finding: broad-except", printed)

    def test_a_missing_file_or_no_findings_leaves_the_prompt_alone(self) -> None:
        """A path that does not exist, or a document without findings, adds nothing."""
        self.assertEqual(self._main(str(self.tmp / "absent.json")), BASE_PROMPT + "\n")
        self.assertEqual(self._main(self._write({"findings": []})), BASE_PROMPT + "\n")
        self.assertEqual(self._main(self._write({})), BASE_PROMPT + "\n")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
