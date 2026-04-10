"""Shared SARIF 2.1.0 normalizer utilities (per design §9.1 §9.2 §A.2.5).

Provides `parse_sarif()` for converting SARIF results into canonical Finding
objects, and a 50MB guard to reject oversized artifacts before parsing.
"""
from __future__ import absolute_import

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer
from scripts.quality.rollup_v2.taxonomy import lookup
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    CATEGORY_GROUP_SECURITY,
    CategoryGroup,
    Finding,
)

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


def _extract_location(result: Dict[str, Any]) -> Tuple[str, int, int | None, int | None]:
    """Extract (file, line, end_line, column) from the first SARIF location."""
    locations = result.get("locations", [])
    if not locations or not isinstance(locations, list):
        return ("unknown", 1, None, None)
    loc = locations[0]
    phys = loc.get("physicalLocation", {}) if isinstance(loc, dict) else {}
    artifact_loc = phys.get("artifactLocation", {}) if isinstance(phys, dict) else {}
    uri = str(artifact_loc.get("uri", "unknown")) if isinstance(artifact_loc, dict) else "unknown"
    region = phys.get("region", {}) if isinstance(phys, dict) else {}
    if not isinstance(region, dict):
        region = {}
    line = int(region.get("startLine", 1))
    end_line = region.get("endLine")
    if end_line is not None:
        end_line = int(end_line)
    column = region.get("startColumn")
    if column is not None:
        column = int(column)
    return (uri, line, end_line, column)


def _extract_context_snippet(result: Dict[str, Any]) -> str:
    """Extract context snippet from SARIF location region."""
    locations = result.get("locations", [])
    if not locations or not isinstance(locations, list):
        return ""
    loc = locations[0]
    if not isinstance(loc, dict):
        return ""
    phys = loc.get("physicalLocation", {})
    if not isinstance(phys, dict):
        return ""
    region = phys.get("region", {})
    if not isinstance(region, dict):
        return ""
    snippet = region.get("snippet", {})
    if isinstance(snippet, dict):
        return str(snippet.get("text", ""))
    return ""


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


def parse_sarif(
    data: Dict[str, Any],
    provider: str,
    repo_root: Path,
    normalizer: BaseNormalizer,
) -> List[Finding]:
    """Parse a SARIF 2.1.0 payload and return canonical Finding objects.

    Parameters
    ----------
    data : dict
        Parsed SARIF JSON (the top-level object with ``runs`` array).
    provider : str
        Provider name for taxonomy lookup and corroborator construction.
    repo_root : Path
        Repository root for path validation.
    normalizer : BaseNormalizer
        The normalizer instance (used for ``_build_finding``).

    Returns
    -------
    List[Finding]
        Canonical findings parsed from all SARIF runs.
    """
    findings: List[Finding] = []
    runs = data.get("runs", [])
    if not isinstance(runs, list):
        return findings

    index = 0
    for run in runs:
        if not isinstance(run, dict):
            continue
        rule_index = _build_rule_index(run)

        results = run.get("results", [])
        if not isinstance(results, list):
            continue

        for result in results:
            if not isinstance(result, dict):
                continue

            rule_id = str(result.get("ruleId", "unknown"))
            rule_meta = rule_index.get(rule_id, {})
            message = _extract_message(result)
            level = str(result.get("level", "warning")).lower()
            severity = _SARIF_LEVEL_TO_SEVERITY.get(level, "medium")
            file_path, line, end_line, column = _extract_location(result)
            category = lookup(provider, rule_id) or rule_id
            tags = _extract_tags(result)
            cwe = _extract_cwe(result)
            category_group = _classify_category_group(category, tags)
            rule_url = _extract_rule_url(rule_meta)
            context_snippet = _extract_context_snippet(result)

            finding = normalizer._build_finding(
                finding_id=f"{provider.lower()}-{index:04d}",
                file=file_path,
                line=line,
                end_line=end_line,
                column=column,
                category=category,
                category_group=category_group,
                severity=severity,
                primary_message=message,
                rule_id=rule_id,
                rule_url=rule_url,
                original_message=message,
                context_snippet=context_snippet,
                cwe=cwe,
            )
            findings.append(finding)
            index += 1

    return findings
