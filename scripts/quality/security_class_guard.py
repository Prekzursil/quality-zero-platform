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
from typing import Any, Iterable, List, Mapping, Tuple


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

_CWE_RE = re.compile(r"\bCWE-\d+\b", re.IGNORECASE)
_OWASP_RE = re.compile(r"\bA0\d:\d{4}\b", re.IGNORECASE)


class SecurityAutoMergeRefusedError(RuntimeError):
    """Raised when QRv2 tries to auto-merge a security-class remediation."""


@dataclass(frozen=True)
class ClassifiedFindings:
    """Result of splitting findings into auto-merge-ok vs must-open-pr sets."""

    auto_merge_ok: List[Mapping[str, Any]]
    must_open_pr: List[Mapping[str, Any]]


def _scanner_name(finding: Mapping[str, Any]) -> str:
    """Extract a scanner identifier from a finding record."""
    for key in ("scanner", "source", "tool", "analyzer"):
        value = finding.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _has_security_tag(finding: Mapping[str, Any]) -> bool:
    """Return True when any finding text references a CWE / OWASP entry."""
    haystack_parts: List[str] = []
    for key in ("id", "rule", "message", "category", "title"):
        value = finding.get(key)
        if isinstance(value, str):
            haystack_parts.append(value)
    tags = finding.get("tags")
    if isinstance(tags, (list, tuple)):
        haystack_parts.extend(str(t) for t in tags if isinstance(t, str))
    haystack = " | ".join(haystack_parts)
    return bool(_CWE_RE.search(haystack) or _OWASP_RE.search(haystack))


def is_security_finding(finding: Mapping[str, Any]) -> bool:
    """Return True when ``finding`` is security-class per any recognised signal."""
    if not isinstance(finding, Mapping):
        return False
    if bool(finding.get("is_security")):
        return True
    scanner = _scanner_name(finding)
    if scanner in SECURITY_SCANNERS:
        return True
    category = str(finding.get("category", "")).strip().lower()
    if category in {"security", "vulnerability", "secret"}:
        return True
    return _has_security_tag(finding)


def filter_auto_merge_candidates(
    findings: Iterable[Mapping[str, Any]],
) -> ClassifiedFindings:
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
            for f in classified.must_open_pr
        )
        raise SecurityAutoMergeRefusedError(
            f"QRv2 refused to auto-merge: security-class findings present "
            f"({ids}). Security-class remediations must open a PR."
        )


if __name__ == "__main__":  # pragma: no cover — ad-hoc CLI
    import json

    _findings = json.loads(sys.stdin.read() or "[]")
    _classified = filter_auto_merge_candidates(_findings)
    print(json.dumps({
        "auto_merge_ok": _classified.auto_merge_ok,
        "must_open_pr": _classified.must_open_pr,
    }, indent=2))
