"""Coverage normalizer (per design §4.2 + §A.6, Task 6.8 special handling)."""
from __future__ import absolute_import

from pathlib import Path
from typing import Any, Iterable

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer, FindingDraft
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    Finding,
)


def _severity_from_percent(percent: float) -> str:
    """Map a coverage percentage to severity.

    <80 -> high, 80-95 -> medium, 95-99 -> low, 100 -> (no finding emitted).
    """
    if percent < 80.0:
        return "high"
    if percent < 95.0:
        return "medium"
    return "low"


class CoverageNormalizer(BaseNormalizer):
    """Normalize coverage artifact JSON into canonical Findings.

    Coverage findings are NOT classic lint findings -- they represent coverage
    percentages per module. The category is always 'coverage-gap' and
    category_group is always 'quality'. Taxonomy lookup is bypassed.

    Only modules with coverage below 100% emit findings.
    """
    provider = "Coverage"

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        components = (artifact or {}).get("components", [])
        for index, component in enumerate(components):
            percent = float(component.get("percent", 100.0))
            if percent >= 100.0:
                continue
            name = str(component.get("name", f"module-{index}"))
            yield self._build_finding(FindingDraft(
                finding_id=f"coverage-{index:04d}",
                file=name,
                line=1,
                category="coverage-gap",
                category_group=CATEGORY_GROUP_QUALITY,
                severity=_severity_from_percent(percent),
                primary_message=f"Coverage {percent:.1f}% below threshold",
                rule_id="coverage-gap",
                rule_url=None,
                original_message=f"Module {name} has {percent:.1f}% coverage",
                context_snippet="",
            ))
