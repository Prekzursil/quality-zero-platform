"""Applitools visual regression normalizer (per design §9.4).

Parses Applitools batch result JSON and converts unresolved, failed,
and mismatch entries into canonical Finding objects.
"""
from __future__ import absolute_import

from pathlib import Path
from typing import Any, Iterable

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    Finding,
)


class ApplitoolsNormalizer(BaseNormalizer):
    """Normalize Applitools batch results into canonical findings."""

    provider = "Applitools"

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        if not isinstance(artifact, dict):
            return  # generator early-exit (StopIteration); return value would be discarded

        batch_url = artifact.get("batchUrl")
        results = artifact.get("results", [])
        if not isinstance(results, list):
            return

        index = 0
        for result in results:
            if not isinstance(result, dict):
                continue

            status = str(result.get("status", "")).lower()
            if status not in ("unresolved", "failed"):
                continue

            test_name = str(result.get("testName", "unknown"))
            result_url = result.get("url") or batch_url
            unresolved_count = int(result.get("unresolvedCount", 0))
            failed_count = int(result.get("failedCount", 0))

            severity = "high" if status == "failed" else "medium"

            yield self._build_finding(
                finding_id=f"applitools-{index:04d}",
                file="(applitools)",
                line=1,
                category="visual-regression-diff",
                category_group=CATEGORY_GROUP_QUALITY,
                severity=severity,
                primary_message=(
                    f"Visual test {test_name!r}: {status} "
                    f"(unresolved={unresolved_count}, failed={failed_count})"
                ),
                rule_id="applitools/visual-diff",
                rule_url=result_url,
                original_message=f"{test_name}: {status}",
                context_snippet="",
            )
            index += 1
