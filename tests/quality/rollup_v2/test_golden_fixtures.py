"""Golden fixture tests for deterministic rendering (per design §A.9 + §B.3.7 + §B.3.16)."""
from __future__ import absolute_import

import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.renderer import render_markdown
from scripts.quality.rollup_v2.types.corroborator import Corroborator
from scripts.quality.rollup_v2.types.finding import SCHEMA_VERSION, Finding

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "renderer"


def _build_findings(count: int, *, unicode: bool = False) -> List[Finding]:
    """Build deterministic findings for golden fixture tests."""
    files = [
        "src/api/auth.py",
        "src/core/models.py",
        "src/utils/helpers.py",
        "tests/test_auth.py",
        "config/settings.py",
    ]
    if unicode:
        files = ["src/caf\u00e9.py", "src/\u65e5\u672c\u8a9e/app.py", "src/api/auth.py"]

    categories = [
        "unused-import", "broad-except", "hardcoded-secret",
        "missing-docstring", "too-complex", "dead-code",
        "unused-variable", "line-too-long",
    ]
    severities = ["critical", "high", "medium", "low", "info"]
    providers = ["QLTY", "SonarCloud", "Codacy", "DeepSource", "DeepScan"]
    patch_sources = ["deterministic", "none", "llm"]

    findings: List[Finding] = []
    for i in range(count):
        f_idx = i % len(files)
        c_idx = i % len(categories)
        s_idx = i % len(severities)
        p_idx = i % len(providers)
        ps_idx = i % len(patch_sources)
        line = 10 + (i * 3) % 100

        corr = Corroborator.from_provider(
            provider=providers[p_idx],
            rule_id=f"rule-{i:03d}",
            rule_url=(
                f"https://rules.example.com/{categories[c_idx]}"
                if i % 3 == 0
                else None
            ),
            original_message=(
                f"\u65e5\u672c\u8a9e message for finding {i}"
                if unicode and i % 2 == 0
                else f"Original message for finding {i}"
            ),
        )

        patch_source = patch_sources[ps_idx]
        patch_text = (
            f"--- a/{files[f_idx]}\n"
            f"+++ b/{files[f_idx]}\n"
            f"@@ -{line},1 +{line},1 @@\n"
            f"-old line {i}\n"
            f"+new line {i}"
            if patch_source != "none"
            else None
        )

        msg = (
            f"\u65e5\u672c\u8a9e: finding {i}"
            if unicode and i % 2 == 0
            else f"Finding {i}: {categories[c_idx]} detected in {files[f_idx]}"
        )

        finding = Finding(
            schema_version=SCHEMA_VERSION,
            finding_id=f"qzp-{i + 1:04d}",
            file=files[f_idx],
            line=line,
            end_line=line,
            column=None,
            category=categories[c_idx],
            category_group=(
                "security" if categories[c_idx] == "hardcoded-secret" else "quality"
            ),
            severity=severities[s_idx],
            corroboration="single",
            primary_message=msg,
            corroborators=(corr,),
            fix_hint=f"Fix hint for finding {i}" if i % 4 == 0 else None,
            patch=patch_text,
            patch_source=patch_source,
            patch_confidence=(
                "high"
                if patch_source == "deterministic"
                else ("medium" if patch_source == "llm" else None)
            ),
            context_snippet=f"context line {i}",
            source_file_hash="",
            cwe=(
                f"CWE-{100 + i}"
                if categories[c_idx] == "hardcoded-secret"
                else None
            ),
            autofixable=(patch_source != "none"),
            tags=(),
        )
        findings.append(finding)
    return findings


def _build_payload(findings: List[Finding]) -> Dict[str, Any]:
    """Build a render-ready payload from findings."""
    # Build provider summaries
    from collections import defaultdict

    by_provider: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"total": 0, "high": 0, "medium": 0, "low": 0}
    )
    for f in findings:
        for c in f.corroborators:
            counts = by_provider[c.provider]
            counts["total"] += 1
            sev = f.severity.lower()
            if sev in counts:
                counts[sev] += 1

    provider_summaries = [
        {"provider": p, **counts} for p, counts in sorted(by_provider.items())
    ]

    return {
        "schema_version": "qzp-rollup/1",
        "total_findings": len(findings),
        "findings": findings,
        "provider_summaries": provider_summaries,
        "normalizer_errors": [],
        "security_drops": [],
    }


def _write_golden(path: Path, content: str) -> None:
    """Write golden file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read_golden(path: Path) -> str:
    """Read golden file."""
    return path.read_text(encoding="utf-8")


class Golden42FindingsTests(unittest.TestCase):
    """Golden fixture: 42 findings across 5 files (per §A.9.2)."""

    GOLDEN_PATH = FIXTURES_DIR / "golden_42_findings.md"

    def test_render_matches_golden_42(self) -> None:
        findings = _build_findings(42)
        payload = _build_payload(findings)
        rendered = render_markdown(payload)

        if not self.GOLDEN_PATH.exists():
            _write_golden(self.GOLDEN_PATH, rendered)
            self.skipTest("Golden file created -- re-run to validate.")

        expected = _read_golden(self.GOLDEN_PATH)
        self.assertEqual(rendered, expected, "Rendered output differs from golden fixture")

    def test_render_is_deterministic(self) -> None:
        """Same input must always produce the same output."""
        findings = _build_findings(42)
        payload = _build_payload(findings)
        render1 = render_markdown(payload)
        render2 = render_markdown(payload)
        self.assertEqual(render1, render2)


class Golden250FindingsTests(unittest.TestCase):
    """Golden fixture: 250 findings exercising truncation (per §B.3.7)."""

    GOLDEN_PATH = FIXTURES_DIR / "golden_250_findings.md"

    def test_render_matches_golden_250(self) -> None:
        findings = _build_findings(250)
        payload = _build_payload(findings)
        rendered = render_markdown(payload)

        if not self.GOLDEN_PATH.exists():
            _write_golden(self.GOLDEN_PATH, rendered)
            self.skipTest("Golden file created -- re-run to validate.")

        expected = _read_golden(self.GOLDEN_PATH)
        self.assertEqual(rendered, expected, "Rendered output differs from golden fixture")

    def test_truncation_applied_for_250(self) -> None:
        """250 findings should trigger truncation (collapse or artifact fallback)."""
        findings = _build_findings(250)
        payload = _build_payload(findings)
        rendered = render_markdown(payload)
        # 250 findings generate a very large output that exceeds _MAX_CHARS,
        # so the renderer falls back to the artifact summary view.
        self.assertIn("250 findings", rendered)
        self.assertIn("Full report is too large", rendered)

    def test_render_250_is_deterministic(self) -> None:
        findings = _build_findings(250)
        payload = _build_payload(findings)
        render1 = render_markdown(payload)
        render2 = render_markdown(payload)
        self.assertEqual(render1, render2)


class GoldenNonAsciiTests(unittest.TestCase):
    """Golden fixture: Unicode safety (per §B.3.16)."""

    GOLDEN_PATH = FIXTURES_DIR / "golden_nonascii.md"

    def test_render_matches_golden_nonascii(self) -> None:
        findings = _build_findings(5, unicode=True)
        payload = _build_payload(findings)
        rendered = render_markdown(payload)

        if not self.GOLDEN_PATH.exists():
            _write_golden(self.GOLDEN_PATH, rendered)
            self.skipTest("Golden file created -- re-run to validate.")

        expected = _read_golden(self.GOLDEN_PATH)
        self.assertEqual(
            rendered, expected, "Rendered output differs from golden fixture"
        )

    def test_nonascii_file_path_preserved(self) -> None:
        findings = _build_findings(5, unicode=True)
        payload = _build_payload(findings)
        rendered = render_markdown(payload)
        self.assertIn("caf\u00e9.py", rendered)

    def test_nonascii_message_preserved(self) -> None:
        findings = _build_findings(5, unicode=True)
        payload = _build_payload(findings)
        rendered = render_markdown(payload)
        self.assertIn("\u65e5\u672c\u8a9e", rendered)

    def test_render_nonascii_is_deterministic(self) -> None:
        findings = _build_findings(5, unicode=True)
        payload = _build_payload(findings)
        render1 = render_markdown(payload)
        render2 = render_markdown(payload)
        self.assertEqual(render1, render2)


if __name__ == "__main__":
    unittest.main()
