"""Codacy normalizer (per design §4.2 + §A.6)."""
from __future__ import absolute_import

from pathlib import Path
from typing import Any, Iterable

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer
from scripts.quality.rollup_v2.taxonomy import lookup
from scripts.quality.rollup_v2.types.finding import (
    CATEGORY_GROUP_QUALITY,
    CATEGORY_GROUP_SECURITY,
    Finding,
)

_SEVERITY_MAP = {
    "Error": "high",
    "Warning": "medium",
    "Info": "low",
}

_SECURITY_CATEGORY_HINTS = frozenset({
    "sql-injection", "command-injection", "hardcoded-password-string",
    "weak-crypto", "insecure-random", "exec-used", "xss",
})


class CodacyNormalizer(BaseNormalizer):
    provider = "Codacy"

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        issues = (artifact or {}).get("issues", [])
        for index, issue in enumerate(issues):
            pattern_id = str(issue.get("patternId", ""))
            category = lookup("Codacy", pattern_id) or "uncategorized"
            group = (
                CATEGORY_GROUP_SECURITY
                if category in _SECURITY_CATEGORY_HINTS
                else CATEGORY_GROUP_QUALITY
            )
            yield self._build_finding(
                finding_id=f"codacy-{index:04d}",
                file=str(issue.get("filename", "")),
                line=int(issue.get("line") or 1),
                category=category,
                category_group=group,
                severity=_SEVERITY_MAP.get(str(issue.get("severity", "Warning")), "medium"),
                primary_message=str(issue.get("message", "")),
                rule_id=pattern_id,
                rule_url=issue.get("patternUrl"),
                original_message=str(issue.get("message", "")),
                context_snippet="",
            )
