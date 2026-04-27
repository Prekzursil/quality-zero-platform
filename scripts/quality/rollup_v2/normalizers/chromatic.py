"""Chromatic visual regression normalizer (per design §9.3).

Parses Chromatic build API response JSON and converts visual changes,
errors, and rejections into canonical Finding objects.
"""
from __future__ import absolute_import

from pathlib import Path
from typing import Any, Iterable

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer, FindingDraft
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    Finding,
)


class ChromaticNormalizer(BaseNormalizer):
    """Normalize Chromatic build results into canonical findings."""

    provider = "Chromatic"

    def _err_draft(self, index: int, err_count: int, web_url: Any) -> FindingDraft:
        return FindingDraft(
            finding_id=f"chromatic-err-{index:04d}",
            file="(chromatic)",
            line=1,
            category="visual-regression-error",
            category_group=CATEGORY_GROUP_QUALITY,
            severity="high",
            primary_message=f"Chromatic build has {err_count} errored snapshot(s)",
            rule_id="chromatic/errored-snapshots",
            rule_url=web_url,
            original_message=f"errCount={err_count}",
            context_snippet="",
        )

    def _change_draft(
        self, index: int, change: dict, web_url: Any, status: str
    ) -> FindingDraft:
        component = str(change.get("component", "unknown"))
        story = str(change.get("story", "unknown"))
        change_url = change.get("changeUrl")
        severity = "high" if status == "REJECTED" else "medium"
        return FindingDraft(
            finding_id=f"chromatic-{index:04d}",
            file="(chromatic)",
            line=1,
            category="visual-regression-diff",
            category_group=CATEGORY_GROUP_QUALITY,
            severity=severity,
            primary_message=f"Visual diff in {component}/{story} ({status})",
            rule_id="chromatic/visual-diff",
            rule_url=change_url or web_url,
            original_message=f"{component}/{story}: {status}",
            context_snippet="",
        )

    def _changes_iter(self, build: dict) -> Iterable[dict]:
        changes = build.get("changes", [])
        if not isinstance(changes, list):
            return
        for change in changes:
            if not isinstance(change, dict):
                continue
            status = str(change.get("status", "")).upper()
            if status in ("CHANGED", "REJECTED"):
                yield {"_change": change, "_status": status}

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        if not isinstance(artifact, dict):
            return  # generator early-exit (StopIteration); return value would be discarded
        builds = artifact.get("builds", [])
        if not isinstance(builds, list):
            return

        index = 0
        for build in builds:
            if not isinstance(build, dict):
                continue
            err_count = int(build.get("errCount", 0))
            web_url = build.get("webUrl")
            if err_count > 0:
                yield self._build_finding(self._err_draft(index, err_count, web_url))
                index += 1
            for entry in self._changes_iter(build):
                yield self._build_finding(
                    self._change_draft(index, entry["_change"], web_url, entry["_status"])
                )
                index += 1
