"""Dependabot normalizer (per design §4.2 + §A.6)."""
from __future__ import absolute_import

from pathlib import Path
from typing import Any, Iterable

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer
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


class DependabotNormalizer(BaseNormalizer):
    """Normalize Dependabot alert artifacts into canonical Findings.

    All Dependabot alerts are security findings. Category is 'vulnerable-dependency'
    since they represent dependency vulnerabilities, not static lint rules.
    Taxonomy lookup is bypassed.
    """
    provider = "Dependabot"

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        alerts = (artifact or {}).get("alerts", [])
        for index, alert in enumerate(alerts):
            advisory = alert.get("security_advisory") or {}
            vulnerability = alert.get("security_vulnerability") or {}
            dependency = alert.get("dependency") or {}

            summary = str(advisory.get("summary", ""))
            raw_severity = str(vulnerability.get("severity", "medium")).lower()
            severity = _SEVERITY_MAP.get(raw_severity, "medium")

            package_info = vulnerability.get("package") or {}
            package_name = str(package_info.get("name", "unknown"))

            manifest_path = str(dependency.get("manifest_path", ""))
            cve_id = advisory.get("cve_id")
            cwes = advisory.get("cwes") or []
            cwe = str(cwes[0].get("cwe_id", "")) if cwes else None

            yield self._build_finding(
                finding_id=f"deps-{index:04d}",
                file=manifest_path,
                line=1,
                category="vulnerable-dependency",
                category_group=CATEGORY_GROUP_SECURITY,
                severity=severity,
                primary_message=f"{package_name}: {summary}",
                rule_id=str(advisory.get("ghsa_id", "")),
                rule_url=None,
                original_message=summary,
                context_snippet="",
                cwe=cwe if cwe else None,
            )
