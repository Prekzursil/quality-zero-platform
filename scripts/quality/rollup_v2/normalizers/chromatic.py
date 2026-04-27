"""Chromatic visual regression normalizer (per design §9.3).

Parses Chromatic build API response JSON and converts visual changes,
errors, and rejections into canonical Finding objects.
"""

from __future__ import absolute_import

from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    Finding,
)

_DIFF_STATUSES = {"CHANGED", "REJECTED"}


class ChromaticNormalizer(BaseNormalizer):
    """Normalize Chromatic build results into canonical findings."""

    provider = "Chromatic"

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:  # noqa: ARG002 — repo_root part of base contract
        """Yield findings for every Chromatic build's errored or changed snapshots."""
        if not isinstance(artifact, dict):
            return
        builds = artifact.get("builds", [])
        if not isinstance(builds, list):
            return

        index = 0
        for build in builds:
            if not isinstance(build, dict):
                continue
            for finding in self._findings_for_build(build, start_index=index):
                yield finding
                index += 1

    def _findings_for_build(
        self, build: Mapping[str, Any], *, start_index: int
    ) -> Iterator[Finding]:
        """Yield every finding for one build (errored snapshots + visual diffs)."""
        index = start_index
        web_url = build.get("webUrl")

        err_count = int(build.get("errCount", 0))
        if err_count > 0:
            yield self._build_errored_snapshots_finding(
                index=index, err_count=err_count, web_url=web_url
            )
            index += 1

        changes = build.get("changes", [])
        if not isinstance(changes, list):
            return
        for change in changes:
            finding = self._maybe_build_change_finding(
                change=change, index=index, default_url=web_url
            )
            if finding is None:
                continue
            yield finding
            index += 1

    def _build_errored_snapshots_finding(
        self, *, index: int, err_count: int, web_url: Any
    ) -> Finding:
        """Build the single 'errored snapshots' finding for one build."""
        return self._build_finding(
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

    def _maybe_build_change_finding(
        self, *, change: Any, index: int, default_url: Any
    ) -> Finding | None:
        """Return a finding for one CHANGED/REJECTED snapshot, or None to skip."""
        if not isinstance(change, dict):
            return None
        status = str(change.get("status", "")).upper()
        if status not in _DIFF_STATUSES:
            return None

        component = str(change.get("component", "unknown"))
        story = str(change.get("story", "unknown"))
        change_url = change.get("changeUrl")
        severity = "high" if status == "REJECTED" else "medium"
        return self._build_finding(
            finding_id=f"chromatic-{index:04d}",
            file="(chromatic)",
            line=1,
            category="visual-regression-diff",
            category_group=CATEGORY_GROUP_QUALITY,
            severity=severity,
            primary_message=f"Visual diff in {component}/{story} ({status})",
            rule_id="chromatic/visual-diff",
            rule_url=change_url or default_url,
            original_message=f"{component}/{story}: {status}",
            context_snippet="",
        )
