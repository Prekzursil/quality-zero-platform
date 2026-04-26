"""Profile normalization."""

from __future__ import absolute_import

from copy import deepcopy
from typing import Any, Dict, List, Mapping

from scripts.quality import profile_coverage_normalization
from scripts.quality.string_helpers import dedupe_strings


def _issue_policy_defaults(mode: str) -> Dict[str, str]:
    """Handle issue policy defaults."""
    zero_mode = mode == "zero"
    return {
        "mode": mode,
        "pr_behavior": "absolute" if zero_mode else "introduced_only",
        "main_behavior": "absolute",
        "baseline_ref": "" if zero_mode else "main",
    }


def _merge_issue_policy_defaults(
    mode: str, payload: Mapping[str, Any]
) -> Dict[str, str]:
    """Handle merge issue policy defaults."""
    defaults = _issue_policy_defaults(mode)
    return {
        "mode": mode,
        "pr_behavior": str(payload.get("pr_behavior", defaults["pr_behavior"])).strip()
        or defaults["pr_behavior"],
        "main_behavior": str(
            payload.get("main_behavior", defaults["main_behavior"])
        ).strip()
        or defaults["main_behavior"],
        "baseline_ref": str(
            payload.get("baseline_ref", defaults["baseline_ref"])
        ).strip()
        or defaults["baseline_ref"],
    }


def normalize_issue_policy(
    raw_issue_policy: Mapping[str, Any] | str | None,
) -> Dict[str, str]:
    """Handle normalize issue policy."""
    if isinstance(raw_issue_policy, str):
        return _issue_policy_defaults(str(raw_issue_policy or "").strip() or "ratchet")

    payload = (
        deepcopy(raw_issue_policy or {}) if isinstance(raw_issue_policy, dict) else {}
    )
    mode = str(payload.get("mode", "ratchet")).strip() or "ratchet"
    return _merge_issue_policy_defaults(mode, payload)


def normalize_deps(raw_deps: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Handle normalize deps."""
    payload = deepcopy(raw_deps or {}) if isinstance(raw_deps, dict) else {}
    return {
        "enabled": bool(payload.get("enabled", False)),
        "policy": str(payload.get("policy", "zero_critical")).strip()
        or "zero_critical",
        "scope": str(payload.get("scope", "runtime")).strip() or "runtime",
    }


def normalize_required_contexts(raw: Mapping[str, Any] | None) -> Dict[str, List[str]]:
    """Handle normalize required contexts."""
    payload = deepcopy(raw or {}) if isinstance(raw, dict) else {}
    always = dedupe_strings(payload.get("always", []))
    pull_request_only = [
        item
        for item in dedupe_strings(payload.get("pull_request_only", []))
        if item not in always
    ]
    required_now = dedupe_strings(
        payload.get("required_now", []) or [*always, *pull_request_only]
    )
    target = dedupe_strings(payload.get("target", []) or [*required_now])
    return {
        "always": always,
        "pull_request_only": pull_request_only,
        "required_now": required_now,
        "target": target,
    }


def merge_required_contexts(
    base: Mapping[str, Any] | None, overlay: Mapping[str, Any] | None
) -> Dict[str, List[str]]:
    """Handle merge required contexts."""
    base_payload = base if isinstance(base, Mapping) else {}
    overlay_payload = overlay if isinstance(overlay, Mapping) else {}
    return normalize_required_contexts(
        {
            "always": [
                *base_payload.get("always", []),
                *overlay_payload.get("always", []),
            ],
            "pull_request_only": [
                *base_payload.get("pull_request_only", []),
                *overlay_payload.get("pull_request_only", []),
            ],
            "required_now": [
                *base_payload.get("required_now", []),
                *overlay_payload.get("required_now", []),
            ],
            "target": [
                *base_payload.get("target", []),
                *overlay_payload.get("target", []),
            ],
        }
    )


def normalize_coverage_inputs(raw_inputs: Any) -> List[Dict[str, Any]]:
    """Handle normalize coverage inputs."""
    return profile_coverage_normalization.normalize_coverage_inputs(raw_inputs)


def infer_coverage_inputs(coverage: Mapping[str, Any] | None) -> List[Dict[str, Any]]:
    """Handle infer coverage inputs."""
    return profile_coverage_normalization.infer_coverage_inputs(coverage)


def infer_required_sources(raw_coverage: Mapping[str, Any] | None) -> List[str]:
    """Handle infer required sources."""
    return profile_coverage_normalization.infer_required_sources(raw_coverage)


def normalize_java_setup(raw_java: Any) -> Dict[str, Any]:
    """Handle normalize java setup."""
    return profile_coverage_normalization.normalize_java_setup(raw_java)


def normalize_coverage_setup(raw_setup: Any) -> Dict[str, Any]:
    """Handle normalize coverage setup."""
    return profile_coverage_normalization.normalize_coverage_setup(raw_setup)


def normalize_coverage_assert_mode(raw_assert_mode: Any) -> Dict[str, str]:
    """Handle normalize coverage assert mode."""
    return profile_coverage_normalization.normalize_coverage_assert_mode(
        raw_assert_mode
    )


def normalize_coverage(raw: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Handle normalize coverage."""
    return profile_coverage_normalization.normalize_coverage(raw)


def normalize_codex_environment(
    raw: Mapping[str, Any] | None, *, verify_command: str
) -> Dict[str, Any]:
    """Handle normalize codex environment."""
    payload = deepcopy(raw or {}) if isinstance(raw, dict) else {}
    return {
        "mode": str(payload.get("mode", "automatic")).strip() or "automatic",
        "verify_command": str(payload.get("verify_command", verify_command)).strip()
        or verify_command,
        "auth_file": str(payload.get("auth_file", "~/.codex/auth.json")).strip()
        or "~/.codex/auth.json",
        "network_profile": str(payload.get("network_profile", "unrestricted")).strip()
        or "unrestricted",
        "methods": str(payload.get("methods", "all")).strip() or "all",
        "runner_labels": dedupe_strings(
            payload.get("runner_labels", ["self-hosted", "codex-trusted"])
        ),
    }


def normalize_codeql(raw: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Normalize CodeQL workflow settings."""
    payload = deepcopy(raw or {}) if isinstance(raw, dict) else {}
    return {
        "enabled": bool(payload.get("enabled", True)),
        "languages": dedupe_strings(payload.get("languages", [])),
        "runner": str(payload.get("runner", "ubuntu-latest")).strip()
        or "ubuntu-latest",
        "build_mode": str(payload.get("build_mode", "none")).strip() or "none",
        "setup": normalize_coverage_setup(payload.get("setup", {})),
    }


def normalize_dependabot(raw: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Normalize Dependabot update settings."""
    payload = deepcopy(raw or {}) if isinstance(raw, dict) else {}
    updates = []
    for item in payload.get("updates", []):
        if not isinstance(item, dict):
            continue
        ecosystem = str(item.get("ecosystem", "")).strip()
        directory = str(item.get("directory", "")).strip()
        if not ecosystem or not directory:
            continue
        updates.append(
            {
                "ecosystem": ecosystem,
                "directory": directory,
            }
        )
    return {
        "enabled": bool(payload.get("enabled", True)),
        "updates": updates,
        "open_pull_requests_limit": int(payload.get("open_pull_requests_limit", 10)),
        "schedule_interval": str(payload.get("schedule_interval", "weekly")).strip()
        or "weekly",
        "labels": dedupe_strings(
            payload.get("labels", ["dependencies", "type:chore", "area:ci"])
        ),
    }


# ---------------------------------------------------------------------------
# v2 schema normalisers (see docs/QZP-V2-DESIGN.md §3).
# Pure helpers; not yet wired into ``_finalize_normalized_profile_sections``.
# They produce the canonical v2 shape from either v1 legacy fields or explicit
# v2 input, so the same downstream code can handle both during migration.
# ---------------------------------------------------------------------------


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
