"""Tests for hybrid dedup + merge (per design §3.3 + §A.3.2)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.schema.corroborator import Corroborator
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    CATEGORY_GROUP_SECURITY,
    CATEGORY_GROUP_STYLE,
    SCHEMA_VERSION,
    Finding,
)


def _make_finding(
    file: str = "a.py",
    line: int = 10,
    category: str = "broad-except",
    category_group: str = CATEGORY_GROUP_QUALITY,
    severity: str = "medium",
    provider: str = "Codacy",
    rule_id: str = "Pylint_W0703",
    finding_id: str = "qzp-0001",
) -> Finding:
    corr = Corroborator.from_provider(provider, rule_id, None, f"msg from {provider}")
    return Finding(
        schema_version=SCHEMA_VERSION,
        finding_id=finding_id,
        file=file,
        line=line,
        end_line=line,
        column=None,
        category=category,
        category_group=category_group,
        severity=severity,
        corroboration="single",
        primary_message=f"message from {provider}",
        corroborators=(corr,),
        fix_hint=None,
        patch=None,
        patch_source="none",
        patch_confidence=None,
        context_snippet="",
        source_file_hash="sha256:abc",
        cwe=None,
        autofixable=False,
        tags=(),
    )


class DedupMergeTests(unittest.TestCase):
    """Tests for dedup() hybrid algorithm."""

    def test_same_file_line_category_security_merged(self):
        """Two security findings at same (file, line, category) → merged."""
        from scripts.quality.rollup_v2.dedup import dedup

        f1 = _make_finding(
            category_group=CATEGORY_GROUP_SECURITY,
            category="sql-injection",
            provider="SonarCloud",
            rule_id="S3649",
            severity="critical",
        )
        f2 = _make_finding(
            category_group=CATEGORY_GROUP_SECURITY,
            category="sql-injection",
            provider="Codacy",
            rule_id="B608",
            severity="high",
        )
        result = dedup([f1, f2])
        self.assertEqual(len(result), 1)

    def test_same_file_line_category_quality_merged(self):
        """Two quality findings at same (file, line, category) → merged."""
        from scripts.quality.rollup_v2.dedup import dedup

        f1 = _make_finding(provider="SonarCloud", rule_id="S1125", severity="medium")
        f2 = _make_finding(provider="Codacy", rule_id="Pylint_W0703", severity="high")
        result = dedup([f1, f2])
        self.assertEqual(len(result), 1)

    def test_same_file_line_different_category_quality_not_merged(self):
        """Two quality findings at same (file, line) but different category → NOT merged."""
        from scripts.quality.rollup_v2.dedup import dedup

        f1 = _make_finding(category="broad-except")
        f2 = _make_finding(category="unused-variable")
        result = dedup([f1, f2])
        self.assertEqual(len(result), 2)

    def test_style_same_file_line_different_category_merged(self):
        """Two style findings at same (file, line) → merged regardless of category."""
        from scripts.quality.rollup_v2.dedup import dedup

        f1 = _make_finding(
            category_group=CATEGORY_GROUP_STYLE,
            category="line-too-long",
            provider="Codacy",
            rule_id="C0301",
        )
        f2 = _make_finding(
            category_group=CATEGORY_GROUP_STYLE,
            category="trailing-whitespace",
            provider="QLTY",
            rule_id="W0311",
        )
        result = dedup([f1, f2])
        self.assertEqual(len(result), 1)

    def test_merge_picks_severity_max(self):
        """Merged finding has severity = max of inputs."""
        from scripts.quality.rollup_v2.dedup import dedup

        f1 = _make_finding(severity="low", provider="Codacy", rule_id="R1")
        f2 = _make_finding(severity="high", provider="SonarCloud", rule_id="R2")
        result = dedup([f1, f2])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].severity, "high")

    def test_merge_sets_corroboration_multi(self):
        """Merged finding has corroboration = 'multi' when ≥2 inputs."""
        from scripts.quality.rollup_v2.dedup import dedup

        f1 = _make_finding(provider="SonarCloud", rule_id="R1")
        f2 = _make_finding(provider="Codacy", rule_id="R2")
        result = dedup([f1, f2])
        self.assertEqual(result[0].corroboration, "multi")

    def test_merge_picks_primary_by_provider_priority(self):
        """Primary is the finding whose corroborator has lowest provider rank."""
        from scripts.quality.rollup_v2.dedup import dedup

        # SonarCloud rank=1, Codacy rank=2 → SonarCloud wins
        f_codacy = _make_finding(provider="Codacy", rule_id="Pylint_W0703", severity="medium")
        f_sonar = _make_finding(provider="SonarCloud", rule_id="S1125", severity="medium")
        result = dedup([f_codacy, f_sonar])
        self.assertEqual(len(result), 1)
        # Primary message should come from SonarCloud
        self.assertEqual(result[0].primary_message, "message from SonarCloud")

    def test_merge_combines_all_corroborators(self):
        """Merged finding has corroborators from all inputs."""
        from scripts.quality.rollup_v2.dedup import dedup

        f1 = _make_finding(provider="SonarCloud", rule_id="R1")
        f2 = _make_finding(provider="Codacy", rule_id="R2")
        f3 = _make_finding(provider="DeepSource", rule_id="R3")
        result = dedup([f1, f2, f3])
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0].corroborators), 3)
        providers = {c.provider for c in result[0].corroborators}
        self.assertEqual(providers, {"SonarCloud", "Codacy", "DeepSource"})

    def test_different_files_not_merged(self):
        """Findings at different files are NOT merged."""
        from scripts.quality.rollup_v2.dedup import dedup

        f1 = _make_finding(file="a.py")
        f2 = _make_finding(file="b.py")
        result = dedup([f1, f2])
        self.assertEqual(len(result), 2)

    def test_different_lines_not_merged(self):
        """Findings at different lines are NOT merged."""
        from scripts.quality.rollup_v2.dedup import dedup

        f1 = _make_finding(line=10)
        f2 = _make_finding(line=20)
        result = dedup([f1, f2])
        self.assertEqual(len(result), 2)


class AssignStableIdsTests(unittest.TestCase):
    """Tests for assign_stable_ids() — Task 7.2."""

    def test_ids_are_qzp_numbered(self):
        from scripts.quality.rollup_v2.dedup import assign_stable_ids

        findings = [
            _make_finding(file="b.py", line=5, category="unused-var"),
            _make_finding(file="a.py", line=10, category="broad-except"),
        ]
        result = assign_stable_ids(findings)
        self.assertEqual(len(result), 2)
        # Sorted by (file, line, category): a.py:10:broad-except < b.py:5:unused-var
        self.assertEqual(result[0].finding_id, "qzp-0001")
        self.assertEqual(result[0].file, "a.py")
        self.assertEqual(result[1].finding_id, "qzp-0002")
        self.assertEqual(result[1].file, "b.py")

    def test_ids_deterministic_on_repeated_calls(self):
        from scripts.quality.rollup_v2.dedup import assign_stable_ids

        findings = [
            _make_finding(file="z.py", line=1, category="c1"),
            _make_finding(file="a.py", line=1, category="c2"),
        ]
        result1 = assign_stable_ids(findings)
        result2 = assign_stable_ids(findings)
        self.assertEqual(result1[0].finding_id, result2[0].finding_id)
        self.assertEqual(result1[1].finding_id, result2[1].finding_id)

    def test_ids_zero_padded_to_four_digits(self):
        from scripts.quality.rollup_v2.dedup import assign_stable_ids

        findings = [_make_finding(file="a.py", line=i, category="c") for i in range(1, 4)]
        result = assign_stable_ids(findings)
        self.assertEqual(result[0].finding_id, "qzp-0001")
        self.assertEqual(result[1].finding_id, "qzp-0002")
        self.assertEqual(result[2].finding_id, "qzp-0003")


class DedupEdgeCaseTests(unittest.TestCase):
    """Edge case tests — Task 7.3."""

    def test_empty_list_returns_empty(self):
        from scripts.quality.rollup_v2.dedup import dedup

        self.assertEqual(dedup([]), [])

    def test_single_finding_returns_as_is(self):
        from scripts.quality.rollup_v2.dedup import dedup

        f = _make_finding()
        result = dedup([f])
        self.assertEqual(len(result), 1)
        self.assertIs(result[0], f)

    def test_all_same_provider_no_merge(self):
        """All findings from same provider at different locations → no merging."""
        from scripts.quality.rollup_v2.dedup import dedup

        findings = [
            _make_finding(file="a.py", line=1, category="c1"),
            _make_finding(file="a.py", line=2, category="c2"),
            _make_finding(file="b.py", line=1, category="c1"),
        ]
        result = dedup(findings)
        self.assertEqual(len(result), 3)

    def test_all_same_provider_same_key_does_merge(self):
        """All findings from same provider at SAME key → do merge (same provider, same spot)."""
        from scripts.quality.rollup_v2.dedup import dedup

        findings = [
            _make_finding(file="a.py", line=1, category="broad-except", provider="Codacy", rule_id="R1"),
            _make_finding(file="a.py", line=1, category="broad-except", provider="Codacy", rule_id="R2"),
        ]
        result = dedup(findings)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0].corroborators), 2)


if __name__ == "__main__":
    unittest.main()
