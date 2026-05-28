"""Alternate-view markdown renderers split from ``renderer`` (§A.1.1).

Houses the by-provider, by-severity, and autofixable views plus their
``<details>`` wrapper so the by-file renderer stays bounded in file-level
complexity. The public names are re-exported from ``renderer`` to preserve the
historical import surface.
"""
from __future__ import absolute_import

from collections import defaultdict
from typing import Dict, List, Sequence

from scripts.quality.rollup_v2.renderer_common import _DETAILS_CLOSE, _severity_emoji
from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.severity import SEVERITY_ORDER


def _render_by_provider_view(findings: Sequence[Finding]) -> str:
    """Render the alternate by-provider view."""
    by_provider: Dict[str, List[Finding]] = defaultdict(list)
    for f in findings:
        for c in f.corroborators:
            by_provider[c.provider].append(f)
    lines: List[str] = []
    for provider in sorted(by_provider):
        pf = by_provider[provider]
        lines.append(f"### {provider} ({len(pf)} finding{'s' if len(pf) != 1 else ''})\n")
        for f in sorted(pf, key=lambda x: (x.file, x.line)):
            lines.append(
                f"- {_severity_emoji(f.severity)} `{f.file}` line {f.line} "
                f"· `{f.category}` · **{f.severity}**"
            )
        lines.append("")
    return "\n".join(lines)


def _render_by_severity_view(findings: Sequence[Finding]) -> str:
    """Render the alternate by-severity view."""
    by_sev: Dict[str, List[Finding]] = defaultdict(list)
    for f in findings:
        by_sev[f.severity.lower()].append(f)
    lines: List[str] = []
    for sev in SEVERITY_ORDER:
        if sev not in by_sev:
            continue
        sf = by_sev[sev]
        lines.append(
            f"### {_severity_emoji(sev)} {sev.capitalize()} ({len(sf)} finding{'s' if len(sf) != 1 else ''})\n"
        )
        for f in sorted(sf, key=lambda x: (x.file, x.line)):
            lines.append(
                f"- `{f.file}` line {f.line} · `{f.category}` "
                f"· {len(f.corroborators)} provider{'s' if len(f.corroborators) != 1 else ''}"
            )
        lines.append("")
    return "\n".join(lines)


def _render_autofixable_view(findings: Sequence[Finding]) -> str:
    """Render the alternate autofixable-only view."""
    fixable = [f for f in findings if f.autofixable]
    if not fixable:
        return "_No autofixable findings._\n"
    lines: List[str] = []
    lines.append(f"**{len(fixable)} autofixable finding{'s' if len(fixable) != 1 else ''}:**\n")
    for f in sorted(fixable, key=lambda x: (x.file, x.line)):
        lines.append(
            f"- {_severity_emoji(f.severity)} `{f.file}` line {f.line} "
            f"· `{f.category}` · **{f.patch_source}**"
        )
    lines.append("")
    return "\n".join(lines)


def _render_alternate_views(findings: Sequence[Finding]) -> str:
    """Render all alternate views wrapped in <details> (§A.1.1)."""
    sections: List[str] = []

    sections.append("<details><summary>View by provider</summary>\n")
    sections.append(_render_by_provider_view(findings))
    sections.append(_DETAILS_CLOSE)

    sections.append("<details><summary>View by severity</summary>\n")
    sections.append(_render_by_severity_view(findings))
    sections.append(_DETAILS_CLOSE)

    sections.append("<details><summary>Autofixable only</summary>\n")
    sections.append(_render_autofixable_view(findings))
    sections.append(_DETAILS_CLOSE)

    return "\n".join(sections)
