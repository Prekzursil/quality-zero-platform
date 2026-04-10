"""Prompt template renderer for LLM fallback patches (per design §A.2.2)."""
from __future__ import absolute_import

from pathlib import Path

from scripts.quality.rollup_v2.redaction import redact_secrets
from scripts.quality.rollup_v2.schema.finding import Finding

_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "llm_patch_prompt.md"


def render_prompt(finding: Finding, context_snippet: str) -> str:
    """Render the LLM patch prompt with finding metadata and redacted context.

    The *context_snippet* is always run through ``redact_secrets()`` before
    embedding, even if the caller already redacted (belt-and-suspenders).
    """
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    safe_snippet = redact_secrets(context_snippet)
    return template.format(
        rule_id=finding.corroborators[0].rule_id if finding.corroborators else "unknown",
        category=finding.category,
        severity=finding.severity,
        file=finding.file,
        line=finding.line,
        primary_message=redact_secrets(finding.primary_message),
        context_snippet=safe_snippet,
    )
