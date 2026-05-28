"""v2 schema normalisers (see docs/QZP-V2-DESIGN.md §3).

Pure helpers split out of :mod:`scripts.quality.profile_normalization` so the
legacy (v1) normalisers and the v2 schema normalisers live in cohesive,
separately-bounded modules. They produce the canonical v2 shape from either v1
legacy fields or explicit v2 input, so the same downstream code can handle both
during migration.

The public names are re-exported from ``profile_normalization`` to preserve the
historical import surface.
"""

from __future__ import absolute_import

from copy import deepcopy
from typing import Any, Dict, List, Mapping

_VALID_SEVERITIES = {"block", "warn", "info"}
_VALID_PHASES = {"shadow", "ratchet", "absolute"}


def normalize_profile_version(raw: Any) -> int:
    """Return the declared profile schema version.

    Unknown / missing / unparsable values fall back to ``1`` so pre-existing
    profiles continue to be treated as the legacy contract.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 1
    return value if value in (1, 2) else 1


def _phase_from_issue_policy(legacy_issue_policy: Mapping[str, Any] | None) -> str:
    """Translate a v1 ``issue_policy.mode`` into a v2 ``mode.phase``."""
    if not isinstance(legacy_issue_policy, Mapping):
        return "absolute"
    raw_mode = str(legacy_issue_policy.get("mode", "")).strip()
    if raw_mode in {"zero", "absolute"}:
        return "absolute"
    if raw_mode == "ratchet":
        return "ratchet"
    return "absolute"


def _normalize_ratchet(raw_ratchet: Any) -> Dict[str, Any]:
    """Return a canonical ratchet sub-structure from user input."""
    payload = (
        deepcopy(raw_ratchet or {}) if isinstance(raw_ratchet, Mapping) else {}
    )
    raw_baseline = payload.get("baseline")
    baseline = raw_baseline if isinstance(raw_baseline, Mapping) else {}
    return {
        "baseline": dict(baseline),
        "target_date": str(payload.get("target_date", "")).strip(),
        "escalation_date": str(payload.get("escalation_date", "")).strip(),
        "on_escalation": str(payload.get("on_escalation", "absolute")).strip()
        or "absolute",
    }


def normalize_mode(
    raw_mode: Mapping[str, Any] | None,
    *,
    legacy_issue_policy: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return the canonical v2 ``mode`` block.

    * When ``raw_mode`` is provided it wins.
    * Otherwise the legacy ``issue_policy.mode`` is translated into a phase.
    * Unknown phases coerce to ``"absolute"`` (strict default, per §10.1).
    """
    payload = deepcopy(raw_mode or {}) if isinstance(raw_mode, Mapping) else {}
    return {
        "phase": _resolve_mode_phase(payload, legacy_issue_policy),
        "shadow_until": _coerce_shadow_until(payload.get("shadow_until")),
        "ratchet": _normalize_ratchet(payload.get("ratchet")),
    }


def _resolve_mode_phase(
    payload: Mapping[str, Any],
    legacy_issue_policy: Mapping[str, Any] | None,
) -> str:
    """Pick the canonical phase from explicit v2 input or the legacy field."""
    phase = str(payload.get("phase", "")).strip()
    if not phase:
        phase = _phase_from_issue_policy(legacy_issue_policy)
    if phase not in _VALID_PHASES:
        return "absolute"
    return phase


def _coerce_shadow_until(raw: Any) -> str | None:
    """Accept an ISO-date string or ``None``; coerce everything else."""
    if raw is None:
        return None
    value = raw if isinstance(raw, str) else str(raw)
    stripped = value.strip()
    return stripped or None


def _coerce_severity(raw: Any) -> str:
    """Map raw scanner severity to one of ``{block, warn, info}``."""
    if isinstance(raw, str):
        cleaned = raw.strip().lower()
        if cleaned in _VALID_SEVERITIES:
            return cleaned
    return "block"


def normalize_scanners(
    raw_scanners: Mapping[str, Any] | None,
    *,
    legacy_enabled_scanners: Mapping[str, Any] | None = None,
) -> Dict[str, Dict[str, str]]:
    """Return the canonical v2 ``scanners`` map (``{name: {severity}}``).

    * Explicit ``raw_scanners`` entries win.
    * Legacy ``enabled_scanners`` entries fill in as ``severity: block`` so
      v1 profiles keep their existing gate semantics.
    """
    canonical: Dict[str, Dict[str, str]] = {}

    if isinstance(legacy_enabled_scanners, Mapping):
        for name, value in legacy_enabled_scanners.items():
            if not bool(value):
                continue
            canonical[str(name)] = {"severity": "block"}

    if isinstance(raw_scanners, Mapping):
        for name, value in raw_scanners.items():
            entry = value if isinstance(value, Mapping) else {}
            severity = _coerce_severity(entry.get("severity"))
            canonical[str(name)] = {"severity": severity}

    return canonical


def normalize_overrides(raw_overrides: Any) -> List[Dict[str, Any]]:
    """Return a canonical list of explicit template overrides.

    Each entry requires ``file`` + ``key`` + ``reason``. Entries missing any
    of these are dropped; see docs/QZP-V2-DESIGN.md §3 for the rationale
    (no silent drift — overrides must be self-describing).
    """
    if not isinstance(raw_overrides, list):
        return []
    canonical: List[Dict[str, Any]] = []
    for item in raw_overrides:
        if not isinstance(item, Mapping):
            continue
        file_name = str(item.get("file", "")).strip()
        key = str(item.get("key", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if not file_name or not key or not reason:
            continue
        canonical.append(
            {
                "file": file_name,
                "key": key,
                "value": item.get("value"),
                "reason": reason,
                "expires": str(item.get("expires", "")).strip(),
            }
        )
    return canonical
