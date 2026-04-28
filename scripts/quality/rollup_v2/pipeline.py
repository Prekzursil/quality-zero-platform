"""Pipeline orchestrator for quality rollup v2 (per design §4.2 + §A.3.5).

Steps: normalize -> collect findings -> dedup -> patch dispatch -> derive autofixable
       -> build canonical payload -> render markdown.
"""
from __future__ import absolute_import

from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Tuple

from scripts.quality.rollup_v2 import patches as patch_dispatcher
from scripts.quality.rollup_v2.dedup import assign_stable_ids, dedup
from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer, NormalizerResult
from scripts.quality.rollup_v2.normalizers.applitools import ApplitoolsNormalizer
from scripts.quality.rollup_v2.normalizers.chromatic import ChromaticNormalizer
from scripts.quality.rollup_v2.normalizers.codacy import CodacyNormalizer
from scripts.quality.rollup_v2.normalizers.codeql import CodeQLNormalizer
from scripts.quality.rollup_v2.normalizers.coverage import CoverageNormalizer
from scripts.quality.rollup_v2.normalizers.deepscan import DeepScanNormalizer
from scripts.quality.rollup_v2.normalizers.deepsource import DeepSourceNormalizer
from scripts.quality.rollup_v2.normalizers.dependabot import DependabotNormalizer
from scripts.quality.rollup_v2.normalizers.qlty import QLTYNormalizer
from scripts.quality.rollup_v2.normalizers.secrets import SecretsNormalizer
from scripts.quality.rollup_v2.normalizers.semgrep import SemgrepNormalizer
from scripts.quality.rollup_v2.normalizers.sentry import SentryNormalizer
from scripts.quality.rollup_v2.normalizers.sonarcloud import SonarCloudNormalizer
from scripts.quality.rollup_v2.renderer import render_markdown
from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchResult

# Normalizer registry: maps artifact key -> normalizer instance
NORMALIZER_REGISTRY: Dict[str, BaseNormalizer] = {
    "applitools": ApplitoolsNormalizer(),
    "chromatic": ChromaticNormalizer(),
    "codacy": CodacyNormalizer(),
    "codeql": CodeQLNormalizer(),
    "coverage": CoverageNormalizer(),
    "deepscan": DeepScanNormalizer(),
    "deepsource": DeepSourceNormalizer(),
    "dependabot": DependabotNormalizer(),
    "qlty": QLTYNormalizer(),
    "secrets": SecretsNormalizer(),
    "semgrep": SemgrepNormalizer(),
    "sentry": SentryNormalizer(),
    "sonarcloud": SonarCloudNormalizer(),
}

# Pre-reserved lane keys (§A.5) — all 4 PR 3 lanes now registered above
RESERVED_LANE_KEYS: Dict[str, str] = {}


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Immutable result of a pipeline run."""

    findings: List[Finding]
    normalizer_errors: List[Dict[str, str]]
    security_drops: List[Dict[str, str]]
    canonical_payload: Dict[str, Any]
    markdown: str


def _derive_autofixable(findings: List[Finding]) -> List[Finding]:
    """Derive autofixable from patch_source (per §A.4.1).

    Called once per pipeline run AFTER patch dispatch.
    autofixable = (patch_source != "none")
    """
    return [
        replace(f, autofixable=(f.patch_source != "none"))
        for f in findings
    ]


def _read_source_file(file_path: str, repo_root: Path) -> str:
    """Read source file content for patch generation, returning empty on error."""
    try:
        full_path = repo_root / file_path
        return full_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""


def _apply_patches(
    findings: List[Finding],
    repo_root: Path,
) -> List[Finding]:
    """Run patch dispatch for each finding and update with results."""
    result: List[Finding] = []
    for f in findings:
        source_content = _read_source_file(f.file, repo_root)
        try:
            patch_result = patch_dispatcher.dispatch(
                f, source_file_content=source_content, repo_root=repo_root
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Error boundary: patch generation failure is non-fatal (§A.6)
            patched = replace(
                f,
                patch_error=f"{exc.__class__.__name__}: {exc}",
            )
            result.append(patched)
            continue

        if isinstance(patch_result, PatchResult):
            patched = replace(
                f,
                patch=patch_result.unified_diff,
                patch_source="deterministic",
                patch_confidence=patch_result.confidence,
            )
            result.append(patched)
        else:
            result.append(f)
    return result


def _build_provider_summaries(findings: List[Finding]) -> List[Dict[str, Any]]:
    """Build per-provider summary counts for the canonical payload."""
    by_provider: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"total": 0, "high": 0, "medium": 0, "low": 0}
    )
    for f in findings:
        for c in f.corroborators:
            counts = by_provider[c.provider]
            counts["total"] += 1
            sev = f.severity.lower()
            if sev in counts:
                counts[sev] += 1
    summaries: List[Dict[str, Any]] = []
    for provider in sorted(by_provider):
        counts = by_provider[provider]
        summaries.append({"provider": provider, **counts})
    return summaries


def _build_not_configured_summaries() -> List[Dict[str, Any]]:
    """Build placeholder summaries for reserved-but-not-configured lanes."""
    return [
        {"provider": label, "status": "not-configured", "total": 0, "high": 0, "medium": 0, "low": 0}
        for label in sorted(RESERVED_LANE_KEYS.values())
    ]


def _finding_to_dict(f: Finding) -> Dict[str, Any]:
    """Serialize a Finding to a JSON-safe dict."""
    return {
        "schema_version": f.schema_version,
        "finding_id": f.finding_id,
        "file": f.file,
        "line": f.line,
        "end_line": f.end_line,
        "column": f.column,
        "category": f.category,
        "category_group": f.category_group,
        "severity": f.severity,
        "corroboration": f.corroboration,
        "primary_message": f.primary_message,
        "corroborators": [
            {
                "provider": c.provider,
                "rule_id": c.rule_id,
                "rule_url": c.rule_url,
                "original_message": c.original_message,
                "provider_priority_rank": c.provider_priority_rank,
            }
            for c in f.corroborators
        ],
        "fix_hint": f.fix_hint,
        "patch": f.patch,
        "patch_source": f.patch_source,
        "patch_confidence": f.patch_confidence,
        "context_snippet": f.context_snippet,
        "source_file_hash": f.source_file_hash,
        "cwe": f.cwe,
        "autofixable": f.autofixable,
        "tags": list(f.tags),
        "patch_error": f.patch_error,
    }


def _normalize_all_artifacts(
    artifacts: Dict[str, Any],
    repo_root: Path,
) -> Tuple[List[Finding], List[Dict[str, str]], List[Dict[str, str]]]:
    """Run every registered normalizer over its matching artifact.

    Returns ``(findings, normalizer_errors, security_drops)``.
    """
    all_findings: List[Finding] = []
    all_errors: List[Dict[str, str]] = []
    all_drops: List[Dict[str, str]] = []
    for key, artifact in artifacts.items():
        normalizer = NORMALIZER_REGISTRY.get(key)
        if normalizer is None:
            continue
        result: NormalizerResult = normalizer.run(artifact=artifact, repo_root=repo_root)
        all_findings.extend(result.findings)
        all_errors.extend(result.normalizer_errors)
        all_drops.extend(result.security_drops)
    return all_findings, all_errors, all_drops


def _build_canonical_payload(
    final_findings: List[Finding],
    all_errors: List[Dict[str, str]],
    all_drops: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Assemble the rollup canonical payload (provider_summaries + meta)."""
    provider_summaries = _build_provider_summaries(final_findings)
    configured_providers = {s["provider"] for s in provider_summaries}
    for placeholder in _build_not_configured_summaries():
        if placeholder["provider"] not in configured_providers:
            provider_summaries.append(placeholder)
    return {
        "schema_version": "qzp-rollup/1",
        "total_findings": len(final_findings),
        "findings": final_findings,
        "provider_summaries": provider_summaries,
        "normalizer_errors": all_errors,
        "security_drops": all_drops,
    }


def run_pipeline(
    artifacts: Dict[str, Any],
    repo_root: Path,
) -> PipelineResult:
    """Run the full quality rollup v2 pipeline.

    The caller is responsible for persisting the resulting ``PipelineResult``
    to whatever output directory it cares about; the pipeline itself is
    pure-functional and never touches the filesystem.

    Steps:
    1. Normalize each artifact via registered normalizers
    2. Collect findings, errors, security drops
    3. Dedup + merge
    4. Patch dispatch
    5. Derive autofixable
    6. Build canonical payload
    7. Render markdown
    """
    all_findings, all_errors, all_drops = _normalize_all_artifacts(artifacts, repo_root)
    deduped = assign_stable_ids(dedup(all_findings))
    patched = _apply_patches(deduped, repo_root)
    final_findings = _derive_autofixable(patched)
    canonical_payload = _build_canonical_payload(final_findings, all_errors, all_drops)
    markdown = render_markdown(canonical_payload)
    json_payload = {
        **canonical_payload,
        "findings": [_finding_to_dict(f) for f in final_findings],
    }
    return PipelineResult(
        findings=final_findings,
        normalizer_errors=all_errors,
        security_drops=all_drops,
        canonical_payload=json_payload,
        markdown=markdown,
    )
