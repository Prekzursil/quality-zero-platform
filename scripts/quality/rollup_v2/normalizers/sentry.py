"""Sentry normalizer (per design §4.2 + §A.6)."""
from __future__ import absolute_import

from pathlib import Path
from typing import Any, Iterable

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer, FindingDraft
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    Finding,
)

_LEVEL_MAP = {
    "fatal": "critical",
    "error": "high",
    "warning": "medium",
    "info": "low",
    "debug": "info",
}


class SentryNormalizer(BaseNormalizer):
    """Normalize Sentry issue artifacts into canonical Findings.

    Sentry artifacts come as a per-project structure with nested issues.
    Category is always 'runtime-error' since Sentry tracks runtime exceptions,
    not static analysis rules. Taxonomy lookup is bypassed.
    """
    provider = "Sentry"

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        projects = (artifact or {}).get("projects", [])
        index = 0
        for project in projects:
            issues = project.get("issues") or []
            for issue in issues:
                issue_id = str(issue.get("id", ""))
                title = str(issue.get("title", ""))
                level = str(issue.get("level", "error")).lower()
                severity = _LEVEL_MAP.get(level, "medium")
                metadata = issue.get("metadata") or {}
                filename = str(metadata.get("filename", ""))
                yield self._build_finding(FindingDraft(
                    finding_id=f"sentry-{index:04d}",
                    file=filename,
                    line=1,
                    category="runtime-error",
                    category_group=CATEGORY_GROUP_QUALITY,
                    severity=severity,
                    primary_message=title,
                    rule_id=f"sentry-issue-{issue_id}",
                    rule_url=None,
                    original_message=title,
                    context_snippet="",
                ))
                index += 1
