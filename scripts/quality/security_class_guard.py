#!/usr/bin/env python3
"""Security-class guard: force QRv2 fixes to PR, never auto-merge.

Phase 4 of ``docs/QZP-V2-DESIGN.md`` §5.3 (last criterion): a QRv2
remediation that touches a security-class finding MUST open a pull
request for human review; auto-merge is prohibited even when the
remediation is trivial.

Security-class findings are recognised by any one of:

* A canonical scanner name that is intrinsically security-focused
  (Dependabot, Semgrep, CodeQL, DeepSource Secrets, Socket Security,
  GitHub secret scanning).
* An ``is_security`` flag on the finding payload.
* A CWE reference (``CWE-<number>``) in the finding id, tags, or
  message — CWE entries are, by definition, security weaknesses.
* An OWASP / ``A0n:`` reference in the id or tags.

``is_security_finding(finding)`` is the primitive; ``filter_auto_merge
_candidates(findings)`` splits a list into ``(auto_merge_ok,
must_open_pr)``. The QRv2 loop calls the splitter before deciding
whether to commit-to-remediation-branch + label-for-auto-merge or
open-pr-and-wait.

``ensure_pr_only_for_security(findings)`` is the assertion the loop
uses as a belt-and-suspenders guard: it raises
``SecurityAutoMergeRefusedError`` if called with a mode ``auto_merge`` AND
any finding is security-class.
"""

from __future__ import absolute_import

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, FrozenSet, Iterable, List, Mapping, Set, Tuple


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()

SECURITY_SCANNERS: Tuple[str, ...] = (
    "dependabot",
    "semgrep",
    "codeql",
    "socket",
    "socket_security",
    "deepsource_secrets",
    "github_secret_scanning",
    "gitleaks",
    "trivy",
    "bandit",
)

# Frozenset for O(1) membership. Canonical rollup findings carry the security
# GROUP in ``category_group`` (the rollup_v2 taxonomy), not a flat ``category``.
_SECURITY_SCANNERS_SET: FrozenSet[str] = frozenset(SECURITY_SCANNERS)
_SECURITY_CATEGORY_GROUPS: FrozenSet[str] = frozenset({"security"})

_CWE_RE = re.compile(r"\bCWE-\d+\b", re.IGNORECASE)
_OWASP_RE = re.compile(r"\bA0\d:\d{4}\b", re.IGNORECASE)


class SecurityAutoMergeRefusedError(RuntimeError):
    """Raised when QRv2 tries to auto-merge a security-class remediation."""


@dataclass(frozen=True)
class ClassifiedFindings:
    """Result of splitting findings into auto-merge-ok vs must-open-pr sets."""

    auto_merge_ok: List[Mapping[str, Any]]
    must_open_pr: List[Mapping[str, Any]]


def _iter_corroborators(
        finding: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """Yield each canonical ``corroborators[]`` entry that is a mapping.

    Centralises the rollup_v2 list-shape guard so callers stay flat: they
    iterate the yielded mappings directly instead of re-nesting the
    ``isinstance(list | tuple)`` + ``isinstance(Mapping)`` checks.
    """
    corroborators = finding.get("corroborators")
    if isinstance(corroborators, list | tuple):
        for corroborator in corroborators:
            if isinstance(corroborator, Mapping):
                yield corroborator


def _scanner_names(finding: Mapping[str, Any]) -> Set[str]:
    """Collect every scanner identifier on a finding (flat AND canonical).

    The flat shape uses ``scanner``/``source``/``tool``/``analyzer``; the
    rollup_v2 canonical shape puts each provider under
    ``corroborators[].provider``. We gather ALL of them so a security scanner in
    any corroborator is recognised (not just the first).
    """
    names: Set[str] = set()
    for key in ("scanner", "source", "tool", "analyzer"):
        value = finding.get(key)
        if isinstance(value, str) and value.strip():
            names.add(value.strip().lower())
    for corroborator in _iter_corroborators(finding):
        provider = corroborator.get("provider")
        if isinstance(provider, str) and provider.strip():
            names.add(provider.strip().lower())
    return names


def _corroborator_texts(finding: Mapping[str, Any]) -> List[str]:
    """Collect ``corroborators[].rule_id``/``original_message`` strings."""
    texts: List[str] = []
    for corroborator in _iter_corroborators(finding):
        for sub_key in ("rule_id", "original_message"):
            sub_value = corroborator.get(sub_key)
            if isinstance(sub_value, str):
                texts.append(sub_value)
    return texts


def _has_security_tag(finding: Mapping[str, Any]) -> bool:
    """Return True when any finding text references a CWE / OWASP entry.

    Reads both flat keys and the canonical shape: ``primary_message`` (the
    canonical message), the dedicated ``cwe`` field, and each
    ``corroborators[].rule_id``/``original_message`` — so e.g. ``cwe="CWE-22"``
    on a CodeQL canonical finding matches.
    """
    haystack_parts: List[str] = []
    for key in ("id", "rule", "message", "primary_message", "category",
                "title", "cwe"):
        value = finding.get(key)
        if isinstance(value, str):
            haystack_parts.append(value)
    tags = finding.get("tags")
    if isinstance(tags, list | tuple):
        haystack_parts.extend(str(t) for t in tags if isinstance(t, str))
    haystack_parts.extend(_corroborator_texts(finding))
    haystack = " | ".join(haystack_parts)
    if _CWE_RE.search(haystack):
        return True
    return bool(_OWASP_RE.search(haystack))


def is_security_finding(finding: Mapping[str, Any]) -> bool:
    """Return True when ``finding`` is security-class per any recognised signal."""
    if not isinstance(finding, Mapping):
        return False
    category = str(finding.get("category", "")).strip().lower()
    category_group = str(finding.get("category_group", "")).strip().lower()
    return any((
        bool(finding.get("is_security")),
        bool(_scanner_names(finding) & _SECURITY_SCANNERS_SET),
        category in {"security", "vulnerability", "secret"},
        category_group in _SECURITY_CATEGORY_GROUPS,
        _has_security_tag(finding),
    ))


def filter_auto_merge_candidates(
    findings: Iterable[Mapping[str, Any]], ) -> ClassifiedFindings:
    """Split ``findings`` into auto-merge-safe vs must-open-pr groups."""
    ok: List[Mapping[str, Any]] = []
    pr: List[Mapping[str, Any]] = []
    for finding in findings:
        if is_security_finding(finding):
            pr.append(finding)
        else:
            ok.append(finding)
    return ClassifiedFindings(auto_merge_ok=ok, must_open_pr=pr)


def ensure_pr_only_for_security(
    findings: Iterable[Mapping[str, Any]],
    *,
    intends_auto_merge: bool,
) -> None:
    """Belt-and-suspenders guard called by the QRv2 loop.

    Raises :class:`SecurityAutoMergeRefusedError` when ``intends_auto_merge``
    is true AND any finding in ``findings`` is security-class. Silent
    when no auto-merge is intended or no security-class findings are
    present.
    """
    if not intends_auto_merge:
        return
    classified = filter_auto_merge_candidates(findings)
    if classified.must_open_pr:
        ids = ", ".join(
            str(f.get("id") or f.get("rule") or "<anonymous>")
            for f in classified.must_open_pr)
        raise SecurityAutoMergeRefusedError(
            f"QRv2 refused to auto-merge: security-class findings present "
            f"({ids}). Security-class remediations must open a PR.")


if __name__ == "__main__":  # pragma: no cover — ad-hoc CLI
    import json

    _findings = json.loads(sys.stdin.read() or "[]")
    _classified = filter_auto_merge_candidates(_findings)
    print(
        json.dumps(
            {
                "auto_merge_ok": _classified.auto_merge_ok,
                "must_open_pr": _classified.must_open_pr,
            },
            indent=2,
        ))
