"""SonarCloud normalizer (per design §4.2 + §A.6)."""
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
    "BLOCKER": "critical",
    "CRITICAL": "high",
    "MAJOR": "medium",
    "MINOR": "low",
    "INFO": "info",
}

_SECURITY_CATEGORY_HINTS = frozenset({
    "sql-injection", "command-injection", "hardcoded-password-string",
    "weak-crypto", "insecure-random", "exec-used", "xss",
})


def _extract_file_from_component(component: str) -> str:
    """Extract the file path from a SonarCloud component key.

    Component keys look like 'project-key:path/to/file.py'.
    """
    if ":" in component:
        return component.split(":", 1)[1]
    return component


class SonarCloudNormalizer(BaseNormalizer):
    provider = "SonarCloud"

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        issues = (artifact or {}).get("issues", [])
        for index, issue in enumerate(issues):
            rule = str(issue.get("rule", ""))
            category = lookup("SonarCloud", rule) or "uncategorized"
            group = (
                CATEGORY_GROUP_SECURITY
                if category in _SECURITY_CATEGORY_HINTS
                else CATEGORY_GROUP_QUALITY
            )
            component = str(issue.get("component", ""))
            file_path = _extract_file_from_component(component)
            yield self._build_finding(
                finding_id=f"sonar-{index:04d}",
                file=file_path,
                line=int(issue.get("line") or 1),
                category=category,
                category_group=group,
                severity=_SEVERITY_MAP.get(str(issue.get("severity", "MAJOR")), "medium"),
                primary_message=str(issue.get("message", "")),
                rule_id=rule,
                rule_url=None,
                original_message=str(issue.get("message", "")),
                context_snippet="",
            )
