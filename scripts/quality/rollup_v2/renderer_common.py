"""Shared primitives for the rollup markdown renderers.

Holds the severity-emoji mapping, the collapsible-section close literal, and the
write-time redaction helper so the by-file renderer (``renderer``) and the
alternate-view renderers (``renderer_views``) can share them without a circular
import.
"""
from __future__ import absolute_import

from typing import Dict

from scripts.quality.rollup_v2.redaction import redact_secrets

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


def _safe(value: str | None) -> str:
    """Belt-and-suspenders: redact every user-content string at write time (§B.1.2)."""
    if not value:
        return ""
    return redact_secrets(value)


def _severity_emoji(severity: str) -> str:
    return _SEVERITY_EMOJI.get(severity.lower(), "\u26aa")
