"""QLTY normalizer (per design §4.2 + §A.6)."""
from __future__ import absolute_import

from pathlib import Path
from typing import Any, Iterable

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer, FindingFields
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    Finding,
)
from scripts.quality.rollup_v2.taxonomy import lookup

_SEVERITY_MAP = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "info": "info",
}


class QLTYNormalizer(BaseNormalizer):
    """Normalize QLTY artifact JSON into canonical Findings.

    QLTY provides severity directly in its issues array.
    """
    provider = "QLTY"

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        issues = (artifact or {}).get("issues", [])
        for index, issue in enumerate(issues):
            rule_id = str(issue.get("rule_id", ""))
            category = lookup("QLTY", rule_id) or "uncategorized"
            raw_severity = str(issue.get("severity", "medium")).lower()
            severity = _SEVERITY_MAP.get(raw_severity, "medium")
            message = str(issue.get("message", ""))
            yield self._build_finding(FindingFields(
                finding_id=f"qlty-{index:04d}",
                file=str(issue.get("file", "")),
                line=int(issue.get("line") or 1),
                category=category,
                category_group=CATEGORY_GROUP_QUALITY,
                severity=severity,
                primary_message=message,
                rule_id=rule_id,
                rule_url=None,
                original_message=message,
                context_snippet="",
            ))
