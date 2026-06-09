"""Tests for renderer high-volume truncation + footer (per design §A.1.2 + §B.3.9 + §B.3.15 + §B.3.8)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path
from typing import List

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.renderer import (
    render_markdown,
)
from scripts.quality.rollup_v2.schema.corroborator import Corroborator
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    SCHEMA_VERSION,
    Finding,
)


def _make_finding(
    file: str = "src/app.py",
    line: int = 10,
    category: str = "broad-except",
    severity: str = "medium",
) -> Finding:
    corr = Corroborator.from_provider(
        provider="Codacy",
        rule_id="rule",
        rule_url=None,
        original_message="msg",
    )
    return Finding(
        schema_version=SCHEMA_VERSION,
        finding_id=f"f-{file}-{line}",
        file=file,
        line=line,
        end_line=line,
        column=None,
        category=category,
        category_group=CATEGORY_GROUP_QUALITY,
        severity=severity,
        corroboration="single",
        primary_message="Test message",
        corroborators=(corr,),
        fix_hint=None,
        patch=None,
        patch_source="none",
        patch_confidence=None,
        context_snippet="# ctx",
        source_file_hash="sha256:abc",
        cwe=None,
        autofixable=False,
        tags=(),
        patch_error=None,
    )


def _many_findings(n_files: int, per_file: int) -> List[Finding]:
    findings = []
    for fi in range(n_files):
        for li in range(per_file):
            findings.append(
                _make_finding(file=f"src/file_{fi:03d}.py", line=li + 1)
            )
    return findings


def _payload(findings: List[Finding]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "total_findings": len(findings),
        "findings": findings,
        "provider_summaries": [
            {"provider": "Codacy", "total": len(findings), "high": 0, "medium": len(findings), "low": 0},
        ],
        "unmapped_rules": [],
        "normalizer_errors": [],
    }


class HighVolumeCollapseTruncationTests(unittest.TestCase):
    def test_250_findings_top_20_visible_rest_collapsed(self):
        """Over 200 findings: first 20 files visible, rest in <details>.

        Use exactly 201 findings (21 files * ~10) to stay under the 60k char
        fallback while still exceeding the 200-finding collapse threshold.
        """
        findings = _many_findings(21, 10)  # 21 files * 10 = 210 findings
        md = render_markdown(_payload(findings))
        # Top 20 files should be visible outside <details>
        # Remaining 1 file should be inside <details>
        self.assertIn("additional file", md)
        self.assertIn("<details><summary>1 additional file", md)

    def test_collapse_tie_break_deterministic(self):
        """Files with same finding count sorted alphabetically."""
        findings = _many_findings(25, 10)  # 25 files * 10 = 250 findings
        md1 = render_markdown(_payload(findings))
        md2 = render_markdown(_payload(findings))
        self.assertEqual(md1, md2)

    def test_below_threshold_no_collapse(self):
        """Under 200 findings: no collapse."""
        findings = _many_findings(10, 5)  # 50 findings
        md = render_markdown(_payload(findings))
        self.assertNotIn("additional files", md)

    def test_exactly_200_no_collapse(self):
        """Exactly 200 findings: no collapse (threshold is >200)."""
        findings = _many_findings(20, 10)  # 200 findings
        md = render_markdown(_payload(findings))
        self.assertNotIn("additional files", md)

    def test_201_findings_triggers_collapse(self):
        """201 findings triggers collapse."""
        findings = _many_findings(21, 10)  # 210 findings, 21 files
        # Override total to 210
        md = render_markdown(_payload(findings))
        self.assertIn("additional file", md)


class CharLimitFallbackTests(unittest.TestCase):
    def test_over_60000_chars_shows_artifact_fallback(self):
        """When output exceeds 60000 chars, switch to summary mode."""
        # Create enough findings to blow past the char limit
        # Each finding generates ~200 chars of markdown, so 400 findings = ~80k
        findings = _many_findings(50, 15)  # 750 findings
        md = render_markdown(_payload(findings))
        # Should contain the exact fallback sentence from the spec
        self.assertIn(
            "Full report is too large for a PR comment",
            md,
        )
        self.assertIn("quality-rollup-full-<sha>.md", md)

    def test_under_char_limit_no_fallback(self):
        findings = _many_findings(5, 3)  # 15 findings
        md = render_markdown(_payload(findings))
        self.assertNotIn("Full report is too large", md)


class FooterTests(unittest.TestCase):
    def test_footer_doc_links_present(self):
        findings = [_make_finding()]
        md = render_markdown(_payload(findings))
        self.assertIn("How to read this report", md)
        self.assertIn("Schema v1", md)
        self.assertIn("Report a format issue", md)

    def test_footer_info_emoji(self):
        findings = [_make_finding()]
        md = render_markdown(_payload(findings))
        self.assertIn("\u2139\ufe0f", md)  # info emoji

    def test_footer_link_targets(self):
        findings = [_make_finding()]
        md = render_markdown(_payload(findings))
        self.assertIn("docs/quality-rollup-guide.md", md)
        self.assertIn("docs/schemas/qzp-finding-v1.md", md)


if __name__ == "__main__":
    unittest.main()
