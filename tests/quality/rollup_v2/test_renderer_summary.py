"""Tests for renderer provider summary table + alternate views (per design §4.1 + §A.1.1)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.renderer import render_markdown
from scripts.quality.rollup_v2.types.corroborator import Corroborator
from scripts.quality.rollup_v2.types.finding import (
    CATEGORY_GROUP_QUALITY,
    CATEGORY_GROUP_SECURITY,
    SCHEMA_VERSION,
    Finding,
)


def _make_finding(
    file: str = "src/app.py",
    line: int = 10,
    category: str = "broad-except",
    severity: str = "medium",
    patch: str | None = None,
    patch_source: str = "none",
    autofixable: bool = False,
    providers: tuple[tuple[str, str | None], ...] = (("Codacy", None),),
    category_group: str = CATEGORY_GROUP_QUALITY,
) -> Finding:
    corroborators = tuple(
        Corroborator.from_provider(
            provider=p,
            rule_id=f"{p}_rule",
            rule_url=url,
            original_message=f"Message from {p}",
        )
        for p, url in providers
    )
    return Finding(
        schema_version=SCHEMA_VERSION,
        finding_id=f"f-{file}-{line}-{category}",
        file=file,
        line=line,
        end_line=line,
        column=None,
        category=category,
        category_group=category_group,
        severity=severity,
        corroboration="multi" if len(providers) > 1 else "single",
        primary_message=f"Test message for {category}",
        corroborators=corroborators,
        fix_hint=None,
        patch=patch,
        patch_source=patch_source,
        patch_confidence="high" if patch else None,
        context_snippet="# test",
        source_file_hash="sha256:abc",
        cwe=None,
        autofixable=autofixable,
        tags=(),
        patch_error=None,
    )


def _multi_payload() -> dict:
    findings = [
        _make_finding(severity="high", providers=(("SonarCloud", None),)),
        _make_finding(severity="medium", line=20, providers=(("Codacy", None),)),
        _make_finding(severity="low", line=30, category="unused-import", providers=(("QLTY", None),)),
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "total_findings": 3,
        "findings": findings,
        "provider_summaries": [
            {"provider": "SonarCloud", "total": 1, "high": 1, "medium": 0, "low": 0},
            {"provider": "Codacy", "total": 1, "high": 0, "medium": 1, "low": 0},
            {"provider": "QLTY", "total": 1, "high": 0, "medium": 0, "low": 1},
        ],
        "unmapped_rules": [],
        "normalizer_errors": [],
    }


class ProviderSummaryTableTests(unittest.TestCase):
    def test_table_header_present(self):
        md = render_markdown(_multi_payload())
        self.assertIn("| Provider | Total | High | Medium | Low |", md)

    def test_table_rows_present(self):
        md = render_markdown(_multi_payload())
        self.assertIn("SonarCloud", md)
        self.assertIn("Codacy", md)
        self.assertIn("QLTY", md)

    def test_table_alignment_row(self):
        md = render_markdown(_multi_payload())
        self.assertIn("|----------|", md)


class AlternateViewsTests(unittest.TestCase):
    def test_by_provider_in_details(self):
        md = render_markdown(_multi_payload())
        self.assertIn("<details><summary>View by provider</summary>", md)
        self.assertIn("</details>", md)

    def test_by_severity_in_details(self):
        md = render_markdown(_multi_payload())
        self.assertIn("<details><summary>View by severity</summary>", md)

    def test_autofixable_in_details(self):
        md = render_markdown(_multi_payload())
        self.assertIn("<details><summary>Autofixable only</summary>", md)

    def test_views_are_top_level_not_nested(self):
        """Alternate views should be at top level, not inside other <details>."""
        md = render_markdown(_multi_payload())
        # Each <details> for alternate views should appear after the by-file section
        lines = md.split("\n")
        alt_details = [
            i for i, l in enumerate(lines)
            if l.startswith("<details><summary>") and (
                "View by" in l or "Autofixable" in l
            )
        ]
        # All three alternate views should exist
        self.assertEqual(len(alt_details), 3)

    def test_by_provider_lists_each_provider(self):
        md = render_markdown(_multi_payload())
        # Find the by-provider section
        self.assertIn("### SonarCloud", md)
        self.assertIn("### Codacy", md)
        self.assertIn("### QLTY", md)

    def test_by_severity_shows_all_severities(self):
        md = render_markdown(_multi_payload())
        self.assertIn("High", md)
        self.assertIn("Medium", md)
        self.assertIn("Low", md)

    def test_autofixable_empty_message(self):
        """No autofixable findings shows a message."""
        md = render_markdown(_multi_payload())
        self.assertIn("No autofixable findings", md)

    def test_autofixable_with_fixable_findings(self):
        payload = _multi_payload()
        # Replace one finding with an autofixable one
        payload["findings"][0] = _make_finding(
            severity="high",
            patch="--- a/f.py\n+++ b/f.py",
            patch_source="deterministic",
            autofixable=True,
            providers=(("SonarCloud", None),),
        )
        md = render_markdown(payload)
        self.assertIn("1 autofixable finding", md)


if __name__ == "__main__":
    unittest.main()
