#!/usr/bin/env python3
"""Markdown section renderers for the Codex prompt.

Split from :mod:`scripts.quality.render_codex_prompt` so the known-issues and
canonical-findings section renderers live in a cohesive module, keeping each
module's file-level complexity bounded. The public names are re-exported from
``render_codex_prompt`` to preserve the historical import surface.
"""

from __future__ import absolute_import

from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from scripts.quality.known_issues import load_known_issues, qrv2_prompt_entries

_KNOWN_ISSUES_ROOT = Path(__file__).resolve().parents[2] / "known-issues"


def _render_known_issues_section(
    registry_path: Path = _KNOWN_ISSUES_ROOT,
) -> str:
    """Render the known-issues registry as a Codex prompt section.

    QRv2 reads this section so the remediation loop applies the
    canonical fix for every recurring false positive instead of
    re-deriving it per run. Only entries with ``feeds_qrv2: true`` +
    a non-empty ``fix_snippet`` appear; the ``qrv2_prompt_entries``
    filter enforces that.

    Returns an empty string when the registry is empty — the caller
    simply omits the section in that case so older profiles don't see
    a dangling heading.
    """
    entries = qrv2_prompt_entries(load_known_issues(registry_path))
    if not entries:
        return ""
    lines: List[str] = [
        "## Known-issues registry",
        "",
        (
            "The entries below are documented false positives / recurring "
            "patterns with verified canonical fixes. When a CI failure "
            "matches one of them, apply the fix_snippet verbatim rather "
            "than inventing a new fix."
        ),
        "",
    ]
    for entry in entries:
        lines.append(f"### {entry.get('id', '?')} — {entry.get('title', '')}")
        lines.append("")
        description = str(entry.get("description", "")).strip()
        if description:
            lines.append(description)
            lines.append("")
        affects = entry.get("affects") or []
        if affects:
            lines.append(f"Affects: {', '.join(str(a) for a in affects)}")
            lines.append("")
        fix_snippet = str(entry.get("fix_snippet", "")).rstrip()
        if fix_snippet:
            lines.append("Canonical fix:")
            lines.append("")
            lines.append("```")
            lines.append(fix_snippet)
            lines.append("```")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _format_providers_line(corroborators: List[Dict[str, Any]]) -> str:
    """Build the human-readable provider list with optional agreement count."""
    provider_names = [c.get("provider", "") for c in corroborators if c.get("provider")]
    provider_str = ", ".join(provider_names) if provider_names else "1 provider"
    if len(provider_names) > 1:
        return f"{provider_str} ({len(provider_names)} agree)"
    return provider_str


def _render_finding_block(finding: Dict[str, Any], file_path: str) -> List[str]:
    """Render a single finding into Markdown bullet lines."""
    category = finding.get("category", "unknown")
    line_num = finding.get("line", "?")
    severity = finding.get("severity", "unknown")
    message = finding.get("primary_message", "")
    fix_hint = finding.get("fix_hint")
    patch_diff = finding.get("patch")
    providers_line = _format_providers_line(finding.get("corroborators", []))

    out: List[str] = [
        f"#### Finding: {category} (line {line_num} in {file_path})",
        f"- **Severity**: {severity}",
        f"- **Message**: {message}",
    ]
    if fix_hint:
        out.append(f"- **Fix hint**: {fix_hint}")
    out.append(f"- **Providers**: {providers_line}")
    if patch_diff:
        out.extend(("- **Suggested patch**:", "```diff", patch_diff.rstrip(), "```"))
    out.append("")
    return out


def _render_canonical_findings_section(findings: List[Dict[str, Any]]) -> str:
    """Render structured canonical findings for the remediation prompt."""
    if not findings:
        return ""

    by_file: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for f in findings:
        by_file[f.get("file", "<unknown>")].append(f)

    lines: List[str] = ["## Findings requiring attention", ""]
    for file_path in sorted(by_file):
        lines.extend((f"### File: {file_path}", ""))
        for finding in by_file[file_path]:
            lines.extend(_render_finding_block(finding, file_path))

    return "\n".join(lines)
