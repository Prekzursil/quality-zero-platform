"""Tests for renderer by-file default view (per design §A.1.1)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path
from typing import List, Tuple

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.renderer import render_markdown
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
    patch: str | None = None,
    patch_source: str = "none",
    autofixable: bool = False,
    providers: Tuple[Tuple[str, str | None], ...] = (("Codacy", None),),
    fix_hint: str | None = None,
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
        fix_hint=fix_hint,
        patch=patch,
        patch_source=patch_source,
        patch_confidence="high" if patch else None,
        context_snippet="# test context",
        source_file_hash="sha256:abc",
        cwe=None,
        autofixable=autofixable,
        tags=(),
        patch_error=None,
    )


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


class RendererByFileTests(unittest.TestCase):
    def test_file_headings_present(self):
        findings = [
            _make_finding(file="src/app.py", line=10),
            _make_finding(file="src/app.py", line=20),
            _make_finding(file="src/utils.py", line=5),
        ]
        md = render_markdown(_payload(findings))
        self.assertIn("### `src/app.py` (2 findings)", md)
        self.assertIn("### `src/utils.py` (1 finding)", md)

    def test_finding_heading_format(self):
        findings = [_make_finding(file="a.py", line=42, severity="high")]
        md = render_markdown(_payload(findings))
        # Should contain the emoji + line + category + severity + provider count
        self.assertIn("line 42", md)
        self.assertIn("`broad-except`", md)
        self.assertIn("**high**", md)
        self.assertIn("1 provider", md)

    def test_severity_emoji_high(self):
        findings = [_make_finding(severity="high")]
        md = render_markdown(_payload(findings))
        self.assertIn("\U0001f534", md)  # red circle

    def test_severity_emoji_medium(self):
        findings = [_make_finding(severity="medium")]
        md = render_markdown(_payload(findings))
        self.assertIn("\U0001f7e1", md)  # yellow circle

    def test_severity_emoji_low(self):
        findings = [_make_finding(severity="low")]
        md = render_markdown(_payload(findings))
        self.assertIn("\u26aa", md)  # white circle

    def test_provider_links_with_url(self):
        findings = [
            _make_finding(
                providers=(("SonarCloud", "https://sonar.io/rule/123"),),
            ),
        ]
        md = render_markdown(_payload(findings))
        self.assertIn("[SonarCloud](https://sonar.io/rule/123)", md)

    def test_provider_links_without_url(self):
        findings = [_make_finding(providers=(("Codacy", None),))]
        md = render_markdown(_payload(findings))
        self.assertIn("Codacy", md)
        # Should NOT be a link since url is None
        self.assertNotIn("[Codacy](", md)

    def test_multi_provider_links(self):
        findings = [
            _make_finding(
                providers=(
                    ("Codacy", None),
                    ("SonarCloud", "https://sonar.io/r"),
                ),
            ),
        ]
        md = render_markdown(_payload(findings))
        self.assertIn("Codacy", md)
        self.assertIn("[SonarCloud](https://sonar.io/r)", md)

    def test_patch_in_fenced_diff(self):
        diff = "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new"
        findings = [
            _make_finding(patch=diff, patch_source="deterministic", autofixable=True),
        ]
        md = render_markdown(_payload(findings))
        self.assertIn("```diff", md)
        self.assertIn(diff, md)

    def test_no_patch_message(self):
        findings = [_make_finding(patch=None)]
        md = render_markdown(_payload(findings))
        self.assertIn("_No automated patch available_", md)

    def test_fix_hint_rendered(self):
        findings = [_make_finding(fix_hint="Use a specific exception type")]
        md = render_markdown(_payload(findings))
        self.assertIn("Use a specific exception type", md)
        self.assertIn("**Fix hint:**", md)

    def test_deterministic_output(self):
        """Same input produces identical output."""
        findings = [
            _make_finding(file="b.py", line=5),
            _make_finding(file="a.py", line=10),
            _make_finding(file="a.py", line=3),
        ]
        md1 = render_markdown(_payload(findings))
        md2 = render_markdown(_payload(findings))
        self.assertEqual(md1, md2)

    def test_file_ordering_most_findings_first(self):
        findings = [
            _make_finding(file="few.py", line=1),
            _make_finding(file="many.py", line=1),
            _make_finding(file="many.py", line=2),
            _make_finding(file="many.py", line=3),
        ]
        md = render_markdown(_payload(findings))
        pos_many = md.index("`many.py`")
        pos_few = md.index("`few.py`")
        self.assertLess(pos_many, pos_few)

    def test_within_file_sorted_by_line(self):
        findings = [
            _make_finding(file="a.py", line=30),
            _make_finding(file="a.py", line=10, category="unused-import"),
            _make_finding(file="a.py", line=20, category="dead-code"),
        ]
        md = render_markdown(_payload(findings))
        pos_10 = md.index("line 10")
        pos_20 = md.index("line 20")
        pos_30 = md.index("line 30")
        self.assertLess(pos_10, pos_20)
        self.assertLess(pos_20, pos_30)

    def test_single_finding_singular_label(self):
        findings = [_make_finding(file="single.py")]
        md = render_markdown(_payload(findings))
        self.assertIn("(1 finding)", md)
        self.assertNotIn("(1 findings)", md)


if __name__ == "__main__":
    unittest.main()
