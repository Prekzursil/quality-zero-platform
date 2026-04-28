"""Dependabot normalizer (per design §4.2 + §A.6)."""
from __future__ import absolute_import

from pathlib import Path
from typing import Any, Dict, Iterable

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer, FindingDraft
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_SECURITY,
    Finding,
)

_SEVERITY_MAP = {
    "critical": "critical",
    "high": "high",
    "moderate": "medium",
    "medium": "medium",
    "low": "low",
}


def _dependabot_finding_draft(index: int, alert: Dict[str, Any]) -> FindingDraft:
    """Convert one Dependabot alert payload into the canonical FindingDraft."""
    advisory = alert.get("security_advisory") or {}
    vulnerability = alert.get("security_vulnerability") or {}
    dependency = alert.get("dependency") or {}
    package_info = vulnerability.get("package") or {}
    cwes = advisory.get("cwes") or []
    summary = str(advisory.get("summary", ""))
    package_name = str(package_info.get("name", "unknown"))
    cwe_id = str(cwes[0].get("cwe_id", "")) if cwes else ""
    return FindingDraft(
        finding_id=f"deps-{index:04d}",
        file=str(dependency.get("manifest_path", "")),
        line=1,
        category="vulnerable-dependency",
        category_group=CATEGORY_GROUP_SECURITY,
        severity=_SEVERITY_MAP.get(
            str(vulnerability.get("severity", "medium")).lower(),
            "medium",
        ),
        primary_message=f"{package_name}: {summary}",
        rule_id=str(advisory.get("ghsa_id", "")),
        rule_url=None,
        original_message=summary,
        context_snippet="",
        cwe=cwe_id or None,
    )


class DependabotNormalizer(BaseNormalizer):
    """Normalize Dependabot alert artifacts into canonical Findings.

    All Dependabot alerts are security findings. Category is 'vulnerable-dependency'
    since they represent dependency vulnerabilities, not static lint rules.
    Taxonomy lookup is bypassed.
    """
    provider = "Dependabot"

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        for index, alert in enumerate((artifact or {}).get("alerts", [])):
            yield self._build_finding(_dependabot_finding_draft(index, alert))
