"""Multi-view markdown renderer for quality rollup (per design §4.1 + §A.1.1 + §A.1.2)."""
from __future__ import absolute_import

from collections import defaultdict
from typing import Any, Dict, List, Sequence, Tuple

from scripts.quality.rollup_v2.redaction import redact_secrets
from scripts.quality.rollup_v2.severity import SEVERITY_ORDER
from scripts.quality.rollup_v2.schema.finding import Finding

# --- Truncation thresholds (§A.1.2 + §B.3.9 + §B.3.15) ---
_MAX_VISIBLE_FILES: int = 20
_MAX_FINDINGS_BEFORE_COLLAPSE: int = 200
_MAX_CHARS: int = 60_000

# Closing ``</details>`` literal for the collapsible-section blocks. Pulled to
# a constant so SonarCloud rule python:S1192 (duplicate string literal) stays
# at zero.
_DETAILS_CLOSE = "</details>\n"

# --- Severity emoji mapping ---
_SEVERITY_EMOJI: Dict[str, str] = {
    "critical": "\U0001f534",  # red circle
    "high": "\U0001f534",      # red circle
    "medium": "\U0001f7e1",    # yellow circle
    "low": "\u26aa",           # white circle
    "info": "\u26aa",          # white circle
}

_ARTIFACT_FALLBACK_SENTENCE = (
    "_Full report is too large for a PR comment; see the "
    "`quality-rollup-full-<sha>.md` workflow artifact for complete details._"
)


def _safe(value: str | None) -> str:
    """Belt-and-suspenders: redact every user-content string at write time (§B.1.2)."""
    if not value:
        return ""
    return redact_secrets(value)


def _provider_label(providers: Sequence[Dict[str, Any]]) -> str:
    """Format provider count with correct pluralization."""
    n = len(providers)
    return f"{n} provider{'s' if n != 1 else ''}"


def _render_empty(payload: Dict[str, Any]) -> str:
    """Render the celebration banner when there are zero findings."""
    n_providers = len(payload.get("provider_summaries", []))
    label = f"{n_providers} provider{'s' if n_providers != 1 else ''}"
    return f"\u2705 **All gates passed \u2014 0 findings across {label}.**\n"


def _render_provider_summary_table(payload: Dict[str, Any]) -> str:
    """Render the provider summary GFM table (§4.1)."""
    summaries = payload.get("provider_summaries", [])
    if not summaries:
        return ""
    lines = [
        "## Provider Summary\n",
        "| Provider | Total | High | Medium | Low |",
        "|----------|------:|-----:|-------:|----:|",
    ]
    for s in summaries:
        lines.append(
            f"| {s['provider']} | {s['total']} | {s.get('high', 0)} "
            f"| {s.get('medium', 0)} | {s.get('low', 0)} |"
        )
    lines.append("")
    return "\n".join(lines)


def _severity_emoji(severity: str) -> str:
    return _SEVERITY_EMOJI.get(severity.lower(), "\u26aa")


def _render_finding_heading(f: Finding) -> str:
    """Render a single finding heading line per §A.1.1."""
    emoji = _severity_emoji(f.severity)
    n_providers = len(f.corroborators)
    return (
        f"#### {emoji} line {f.line} \u00b7 `{f.category}` "
        f"\u00b7 **{f.severity}** \u00b7 {n_providers} provider{'s' if n_providers != 1 else ''}"
    )


def _render_provider_links(f: Finding) -> str:
    """Render provider links for a finding."""
    parts = []
    for c in f.corroborators:
        if c.rule_url:
            parts.append(f"[{c.provider}]({c.rule_url})")
        else:
            parts.append(c.provider)
    return " \u00b7 ".join(parts)


def _render_finding_body(f: Finding) -> str:
    """Render the body of a single finding (message, providers, patch)."""
    lines = []
    lines.append(_render_finding_heading(f))
    lines.append("")
    lines.append(f"**Message:** {_safe(f.primary_message)}")
    lines.append("")
    lines.append(f"**Providers:** {_render_provider_links(f)}")
    if f.fix_hint:
        lines.append("")
        lines.append(f"**Fix hint:** {_safe(f.fix_hint)}")
    lines.append("")
    if f.patch:
        lines.append("```diff")
        lines.append(_safe(f.patch))
        lines.append("```")
    else:
        lines.append("_No automated patch available_")
    lines.append("")
    return "\n".join(lines)


def _group_findings_by_file(findings: Sequence[Finding]) -> List[Tuple[str, List[Finding]]]:
    """Group findings by file, sorted by (finding_count DESC, file_path ASC)."""
    by_file: Dict[str, List[Finding]] = defaultdict(list)
    for f in findings:
        by_file[f.file].append(f)
    # Sort: most findings first, then alphabetical for tie-break
    return sorted(by_file.items(), key=lambda item: (-len(item[1]), item[0]))


def _render_by_file_view(
    findings: Sequence[Finding],
    *,
    collapse_after: int | None = None,
) -> str:
    """Render the default by-file view (§A.1.1)."""
    grouped = _group_findings_by_file(findings)
    lines: List[str] = []

    for idx, (file_path, file_findings) in enumerate(grouped):
        if collapse_after is not None and idx == collapse_after:
            remaining_files = len(grouped) - collapse_after
            remaining_findings = sum(len(ff) for _, ff in grouped[collapse_after:])
            lines.append(
                f"<details><summary>{remaining_files} additional files "
                f"({remaining_findings} findings)</summary>\n"
            )

        n = len(file_findings)
        lines.append(f"### `{file_path}` ({n} finding{'s' if n != 1 else ''})\n")
        # Sort findings within file by line number for determinism
        for f in sorted(file_findings, key=lambda x: (x.line, x.category)):
            lines.append(_render_finding_body(f))

    if collapse_after is not None and len(grouped) > collapse_after:
        lines.append(_DETAILS_CLOSE)

    return "\n".join(lines)


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
                f"\u00b7 `{f.category}` \u00b7 **{f.severity}**"
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
                f"- `{f.file}` line {f.line} \u00b7 `{f.category}` "
                f"\u00b7 {len(f.corroborators)} provider{'s' if len(f.corroborators) != 1 else ''}"
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
            f"\u00b7 `{f.category}` \u00b7 **{f.patch_source}**"
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


def _render_footer() -> str:
    """Render the doc-links footer (§A.1.1 + §B.3.8 + §B.3.10)."""
    return (
        "\u2139\ufe0f [How to read this report](docs/quality-rollup-guide.md) "
        "\u00b7 [Schema v1](docs/schemas/qzp-finding-v1.md) "
        "\u00b7 [Report a format issue]"
        "(https://github.com/user/quality-zero-platform/issues/new?labels=rollup-format)\n"
    )


def _render_normalizer_errors(payload: Dict[str, Any]) -> str:
    """Render normalizer error banner if any errors occurred."""
    errors = payload.get("normalizer_errors", [])
    if not errors:
        return ""
    lines = [
        "> \u26a0\ufe0f **Normalizer errors occurred during this run:**\n>",
    ]
    for err in errors:
        lines.append(f"> - {_safe(str(err))}")
    lines.append("")
    return "\n".join(lines)


def render_markdown(payload: Dict[str, Any]) -> str:
    """Render the full multi-view markdown report from a rollup payload.

    Deterministic: same input always produces the same output.
    Belt-and-suspenders: all user-content strings are redacted before emission.
    """
    total = payload.get("total_findings", 0)
    findings: List[Finding] = payload.get("findings", [])

    # Empty state (only celebrate if there are also no normalizer errors)
    has_normalizer_errors = bool(payload.get("normalizer_errors"))
    if total == 0 and not findings and not has_normalizer_errors:
        return _render_empty(payload)

    parts: List[str] = []

    # Normalizer error banner
    parts.append(_render_normalizer_errors(payload))

    # Provider summary table
    parts.append(_render_provider_summary_table(payload))

    # High-volume truncation check
    needs_collapse = total > _MAX_FINDINGS_BEFORE_COLLAPSE or len(findings) > _MAX_FINDINGS_BEFORE_COLLAPSE
    collapse_after = _MAX_VISIBLE_FILES if needs_collapse else None

    # Default by-file view
    by_file_md = _render_by_file_view(findings, collapse_after=collapse_after)
    parts.append(by_file_md)

    # Alternate views
    parts.append(_render_alternate_views(findings))

    # Footer
    parts.append(_render_footer())

    full = "\n".join(parts)

    # Character limit fallback (§B.3.9)
    if len(full) > _MAX_CHARS:
        summary_parts = []
        summary_parts.append(_render_normalizer_errors(payload))
        summary_parts.append(_render_provider_summary_table(payload))
        summary_parts.append(
            f"**{total} findings across "
            f"{len(payload.get('provider_summaries', []))} providers.**\n"
        )
        summary_parts.append(f"\n{_ARTIFACT_FALLBACK_SENTENCE}\n")
        summary_parts.append(_render_footer())
        return "\n".join(summary_parts)

    return full
