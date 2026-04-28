"""DeepScan normalizer (per design §4.2 + §A.6)."""
from __future__ import absolute_import

from pathlib import Path
from typing import Any, Iterable

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer, FindingDraft
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    Finding,
)
from scripts.quality.rollup_v2.taxonomy import lookup


class DeepScanNormalizer(BaseNormalizer):
    """Normalize DeepScan alarm artifacts into canonical Findings.

    DeepScan does not provide per-alarm severity, so all findings default
    to 'medium' per the plan's severity map.
    """
    provider = "DeepScan"

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        alarms = (artifact or {}).get("alarms", [])
        for index, alarm in enumerate(alarms):
            alarm_name = str(alarm.get("name", ""))
            category = lookup("DeepScan", alarm_name) or "uncategorized"
            message = str(alarm.get("message", ""))
            yield self._build_finding(FindingDraft(
                finding_id=f"deepscan-{index:04d}",
                file=str(alarm.get("file", "")),
                line=int(alarm.get("line") or 1),
                category=category,
                category_group=CATEGORY_GROUP_QUALITY,
                severity="medium",
                primary_message=message,
                rule_id=alarm_name,
                rule_url=None,
                original_message=message,
                context_snippet="",
            ))
