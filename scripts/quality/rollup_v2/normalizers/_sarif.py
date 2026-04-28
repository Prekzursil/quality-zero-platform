"""Shared SARIF 2.1.0 normalizer utilities (per design §9.1 §9.2 §A.2.5).

Provides `parse_sarif()` for converting SARIF results into canonical Finding
objects, a 50MB guard to reject oversized artifacts before parsing, and a
``SarifBackedNormalizer`` base class so per-provider SARIF normalizers (CodeQL,
Semgrep) only need to declare ``provider`` instead of duplicating the same
artifact-dispatch ``parse()`` implementation.
"""
from __future__ import absolute_import

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer, FindingDraft
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    CATEGORY_GROUP_SECURITY,
    CategoryGroup,
    Finding,
)
from scripts.quality.rollup_v2.taxonomy import lookup

MAX_SARIF_BYTES: int = 50 * 1024 * 1024  # 50 MB


class SarifTooLargeError(ValueError):
    """Raised when a SARIF artifact exceeds MAX_SARIF_BYTES."""


_SARIF_LEVEL_TO_SEVERITY: Dict[str, str] = {
    "error": "high",
    "warning": "medium",
    "note": "low",
    "none": "low",
}

_SECURITY_TAGS: frozenset[str] = frozenset({
    "security", "vulnerability", "cwe", "injection", "xss",
    "command-injection", "sql-injection", "code-injection",
    "weak-crypto", "hardcoded-secret",
})


def _classify_category_group(
    category: str,
    tags: Tuple[str, ...],
) -> CategoryGroup:
    """Determine category_group from the category and tag set."""
    lower_cat = category.lower()
    lower_tags = {t.lower() for t in tags}
    if lower_cat in _SECURITY_TAGS or lower_tags & _SECURITY_TAGS:
        return CATEGORY_GROUP_SECURITY
    return CATEGORY_GROUP_QUALITY


def _extract_tags(result: Dict[str, Any]) -> Tuple[str, ...]:
    """Extract tags from a SARIF result's properties bag."""
    props = result.get("properties", {})
    if not isinstance(props, dict):
        return ()
    raw_tags = props.get("tags", [])
    if not isinstance(raw_tags, list):
        return ()
    return tuple(str(t) for t in raw_tags if isinstance(t, str))


def _extract_cwe(result: Dict[str, Any]) -> str | None:
    """Extract CWE identifier from SARIF properties or tags."""
    props = result.get("properties", {})
    if isinstance(props, dict):
        cwe = props.get("cwe")
        if isinstance(cwe, str) and cwe:
            return cwe
    tags = _extract_tags(result)
    for tag in tags:
        if tag.upper().startswith("CWE-"):
            return tag
    return None


def _safe_dict(value: Any) -> Dict[str, Any]:
    """Return ``value`` if it's a dict; otherwise an empty dict."""
    return value if isinstance(value, dict) else {}


def _coerce_optional_int(value: Any) -> int | None:
    """Coerce ``value`` to ``int`` only if it's not ``None``."""
    return int(value) if value is not None else None


def _extract_location(result: Dict[str, Any]) -> Tuple[str, int, int | None, int | None]:
    """Extract (file, line, end_line, column) from the first SARIF location."""
    locations = result.get("locations", [])
    if not locations or not isinstance(locations, list):
        return ("unknown", 1, None, None)
    phys = _safe_dict(_safe_dict(locations[0]).get("physicalLocation"))
    artifact_loc = _safe_dict(phys.get("artifactLocation"))
    region = _safe_dict(phys.get("region"))
    return (
        str(artifact_loc.get("uri", "unknown")),
        int(region.get("startLine", 1)),
        _coerce_optional_int(region.get("endLine")),
        _coerce_optional_int(region.get("startColumn")),
    )


def _extract_context_snippet(result: Dict[str, Any]) -> str:
    """Extract context snippet from SARIF location region."""
    locations = result.get("locations") or []
    if not isinstance(locations, list) or not locations:
        return ""
    cursor: Any = locations[0]
    for key in ("physicalLocation", "region", "snippet"):
        if not isinstance(cursor, dict):
            return ""
        cursor = cursor.get(key, {})
    return str(cursor.get("text", "")) if isinstance(cursor, dict) else ""


def _build_rule_index(run: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Build a rule-id-to-metadata index from tool.driver.rules in a SARIF run."""
    rule_index: Dict[str, Dict[str, Any]] = {}
    tool = run.get("tool", {})
    if not isinstance(tool, dict):
        return rule_index
    driver = tool.get("driver", {})
    if not isinstance(driver, dict):
        return rule_index
    rules = driver.get("rules", [])
    if not isinstance(rules, list):
        return rule_index
    for rule in rules:
        if isinstance(rule, dict):
            rid = rule.get("id", "")
            if rid:
                rule_index[rid] = rule
    return rule_index


def _extract_message(result: Dict[str, Any]) -> str:
    """Extract the message text from a SARIF result."""
    message_obj = result.get("message", {})
    if isinstance(message_obj, dict):
        return str(message_obj.get("text", ""))
    if isinstance(message_obj, str):
        return message_obj
    return ""


def _extract_rule_url(rule_meta: Dict[str, Any]) -> str | None:
    """Extract rule help URL from rule metadata."""
    help_uri = rule_meta.get("helpUri")
    if isinstance(help_uri, str) and help_uri:
        return help_uri
    return None


def check_sarif_size(data: bytes | str) -> None:
    """Raise SarifTooLargeError if the raw SARIF data exceeds 50MB."""
    size = len(data) if isinstance(data, bytes) else len(data.encode("utf-8"))
    if size > MAX_SARIF_BYTES:
        raise SarifTooLargeError(
            f"SARIF artifact is {size:,} bytes, exceeding the {MAX_SARIF_BYTES:,} byte limit"
        )


def _build_sarif_finding_draft(
    *,
    result: Dict[str, Any],
    rule_meta: Dict[str, Any],
    provider: str,
    index: int,
) -> FindingDraft:
    """Translate one SARIF ``result`` into a normaliser-ready FindingDraft."""
    rule_id = str(result.get("ruleId", "unknown"))
    message = _extract_message(result)
    level = str(result.get("level", "warning")).lower()
    file_path, line, end_line, column = _extract_location(result)
    category = lookup(provider, rule_id) or rule_id
    tags = _extract_tags(result)
    return FindingDraft(
        finding_id=f"{provider.lower()}-{index:04d}",
        file=file_path,
        line=line,
        end_line=end_line,
        column=column,
        category=category,
        category_group=_classify_category_group(category, tags),
        severity=_SARIF_LEVEL_TO_SEVERITY.get(level, "medium"),
        primary_message=message,
        rule_id=rule_id,
        rule_url=_extract_rule_url(rule_meta),
        original_message=message,
        context_snippet=_extract_context_snippet(result),
        cwe=_extract_cwe(result),
    )


def _iter_sarif_results(runs: List[Any]) -> Iterable[Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]]:
    """Yield ``(result, rule_index)`` pairs from valid SARIF runs."""
    for run in runs:
        if not isinstance(run, dict):
            continue
        rule_index = _build_rule_index(run)
        results = run.get("results", [])
        if not isinstance(results, list):
            continue
        for result in results:
            if isinstance(result, dict):
                yield result, rule_index


def parse_sarif(
    data: Dict[str, Any],
    provider: str,
    normalizer: BaseNormalizer,
) -> List[Finding]:
    """Parse a SARIF 2.1.0 payload and return canonical Finding objects.

    SARIF artifact paths are taken from ``artifactLocation.uri`` as-is —
    the rollup pipeline's path-safety check (``_base.run`` →
    ``validate_finding_file``) handles repo-root validation downstream,
    so this parser does not need ``repo_root`` itself.
    """
    runs = data.get("runs", [])
    if not isinstance(runs, list):
        return []
    findings: List[Finding] = []
    for index, (result, rule_index) in enumerate(_iter_sarif_results(runs)):
        rule_id = str(result.get("ruleId", "unknown"))
        rule_meta = rule_index.get(rule_id, {})
        draft = _build_sarif_finding_draft(
            result=result,
            rule_meta=rule_meta,
            provider=provider,
            index=index,
        )
        findings.append(normalizer._build_finding(draft))
    return findings


class SarifBackedNormalizer(BaseNormalizer):
    """Common ``parse()`` for normalizers whose artifact is SARIF 2.1.0.

    Subclasses only need to set ``provider``. The ``parse`` implementation
    accepts a ``dict`` (already-parsed SARIF JSON), ``str`` (raw JSON text),
    or ``bytes`` (raw JSON bytes); other types yield no findings.
    """

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        if isinstance(artifact, str | bytes):
            check_sarif_size(artifact)
            data = json.loads(artifact)
        elif isinstance(artifact, dict):
            data = artifact
        else:
            return []
        del repo_root  # forwarded by BaseNormalizer.run() but unused here
        return parse_sarif(data, self.provider, self)
