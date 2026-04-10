"""DeepSource normalizer (per design §4.2 + §A.6)."""
from __future__ import absolute_import

from pathlib import Path
from typing import Any, Iterable

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer
from scripts.quality.rollup_v2.taxonomy import lookup
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    CATEGORY_GROUP_SECURITY,
    Finding,
)

_SEVERITY_MAP = {
    "CRITICAL": "high",
    "MAJOR": "medium",
    "MINOR": "low",
}

_SECURITY_CATEGORY_HINTS = frozenset({
    "sql-injection", "command-injection", "hardcoded-password-string",
    "weak-crypto", "insecure-random", "exec-used", "xss",
})


class DeepSourceNormalizer(BaseNormalizer):
    provider = "DeepSource"

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        issues = (artifact or {}).get("issues", [])
        for index, issue in enumerate(issues):
            issue_code = str(issue.get("issue_code", ""))
            category = lookup("DeepSource", issue_code) or "uncategorized"
            group = (
                CATEGORY_GROUP_SECURITY
                if category in _SECURITY_CATEGORY_HINTS
                else CATEGORY_GROUP_QUALITY
            )
            location = issue.get("location") or {}
            position = location.get("position") or {}
            begin = position.get("begin") or {}
            file_path = str(location.get("path", ""))
            line = int(begin.get("line") or 1)
            title = str(issue.get("title", ""))
            yield self._build_finding(
                finding_id=f"deepsource-{index:04d}",
                file=file_path,
                line=line,
                category=category,
                category_group=group,
                severity=_SEVERITY_MAP.get(str(issue.get("severity", "MAJOR")), "medium"),
                primary_message=title,
                rule_id=issue_code,
                rule_url=None,
                original_message=title,
                context_snippet="",
            )
