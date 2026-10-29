"""Profile normalization."""

from __future__ import absolute_import

from copy import deepcopy
from typing import Any, Dict, List, Mapping

from scripts.quality import profile_coverage_normalization
from scripts.quality.string_helpers import dedupe_strings

#: Dependabot's documented ``versioning-strategy`` values, and the ecosystems
#: the option is "Supported by" (docs.github.com, Dependabot options reference).
#: The docs define no per-ecosystem restriction on the values themselves.
DEPENDABOT_VERSIONING_STRATEGIES = frozenset({"auto", "increase", "increase-if-necessary", "lockfile-only", "widen"})
DEPENDABOT_VERSIONING_ECOSYSTEMS = frozenset({"bundler", "cargo", "composer", "helm", "mix", "npm", "pip", "pub", "uv"})


def validate_versioning_strategy(ecosystem: str, directory: str, strategy: str) -> None:
    """Raise ``ValueError`` for a strategy Dependabot would reject.

    Called while normalising every profile, so each rendering path (the
    managed-baseline renderer and the drift-sync Jinja templates alike) fails
    loudly instead of writing a dependabot.yml that stops dependency updates.
    """
    if strategy not in DEPENDABOT_VERSIONING_STRATEGIES:
        raise ValueError(
            f"unsupported Dependabot versioning-strategy {strategy!r} for {ecosystem} {directory}; "
            f"expected one of {sorted(DEPENDABOT_VERSIONING_STRATEGIES)}"
        )
    if ecosystem not in DEPENDABOT_VERSIONING_ECOSYSTEMS:
        raise ValueError(
            f"Dependabot versioning-strategy is not supported for the {ecosystem!r} ecosystem ({directory}); "
            f"supported: {sorted(DEPENDABOT_VERSIONING_ECOSYSTEMS)}"
        )


def _issue_policy_defaults(mode: str) -> Dict[str, str]:
    """Handle issue policy defaults."""
    zero_mode = mode == "zero"
    return {
        "mode": mode,
        "pr_behavior": "absolute" if zero_mode else "introduced_only",
        "main_behavior": "absolute",
        "baseline_ref": "" if zero_mode else "main",
    }


def _merge_issue_policy_defaults(mode: str, payload: Mapping[str, Any]) -> Dict[str, str]:
    """Handle merge issue policy defaults."""
    defaults = _issue_policy_defaults(mode)
    return {
        "mode": mode,
        "pr_behavior": str(payload.get("pr_behavior", defaults["pr_behavior"])).strip() or defaults["pr_behavior"],
        "main_behavior": str(payload.get("main_behavior", defaults["main_behavior"])).strip()
        or defaults["main_behavior"],
        "baseline_ref": str(payload.get("baseline_ref", defaults["baseline_ref"])).strip() or defaults["baseline_ref"],
    }


def normalize_issue_policy(
    raw_issue_policy: Mapping[str, Any] | str | None,
) -> Dict[str, str]:
    """Handle normalize issue policy."""
    if isinstance(raw_issue_policy, str):
        return _issue_policy_defaults(str(raw_issue_policy or "").strip() or "ratchet")

    payload = deepcopy(raw_issue_policy or {}) if isinstance(raw_issue_policy, dict) else {}
    mode = str(payload.get("mode", "ratchet")).strip() or "ratchet"
    return _merge_issue_policy_defaults(mode, payload)


def normalize_deps(raw_deps: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Handle normalize deps."""
    payload = deepcopy(raw_deps or {}) if isinstance(raw_deps, dict) else {}
    return {
        "enabled": bool(payload.get("enabled", False)),
        "policy": str(payload.get("policy", "zero_critical")).strip() or "zero_critical",
        "scope": str(payload.get("scope", "runtime")).strip() or "runtime",
    }


def normalize_required_contexts(raw: Mapping[str, Any] | None) -> Dict[str, List[str]]:
    """Handle normalize required contexts."""
    payload = deepcopy(raw or {}) if isinstance(raw, dict) else {}
    always = dedupe_strings(payload.get("always", []))
    pull_request_only = [item for item in dedupe_strings(payload.get("pull_request_only", [])) if item not in always]
    required_now = dedupe_strings(payload.get("required_now", []) or [*always, *pull_request_only])
    target = dedupe_strings(payload.get("target", []) or [*required_now])
    return {
        "always": always,
        "pull_request_only": pull_request_only,
        "required_now": required_now,
        "target": target,
    }


def merge_required_contexts(base: Mapping[str, Any] | None, overlay: Mapping[str, Any] | None) -> Dict[str, List[str]]:
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
    return profile_coverage_normalization.normalize_coverage_assert_mode(raw_assert_mode)


def normalize_coverage(raw: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Handle normalize coverage."""
    return profile_coverage_normalization.normalize_coverage(raw)


def normalize_codex_environment(raw: Mapping[str, Any] | None, *, verify_command: str) -> Dict[str, Any]:
    """Handle normalize codex environment."""
    payload = deepcopy(raw or {}) if isinstance(raw, dict) else {}
    return {
        "mode": str(payload.get("mode", "automatic")).strip() or "automatic",
        "verify_command": str(payload.get("verify_command", verify_command)).strip() or verify_command,
        "auth_file": str(payload.get("auth_file", "~/.codex/auth.json")).strip() or "~/.codex/auth.json",
        "network_profile": str(payload.get("network_profile", "unrestricted")).strip() or "unrestricted",
        "methods": str(payload.get("methods", "all")).strip() or "all",
        "runner_labels": dedupe_strings(payload.get("runner_labels", ["self-hosted", "codex-trusted"])),
    }


def normalize_codeql(raw: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Normalize CodeQL workflow settings."""
    payload = deepcopy(raw or {}) if isinstance(raw, dict) else {}
    return {
        "enabled": bool(payload.get("enabled", True)),
        "languages": dedupe_strings(payload.get("languages", [])),
        "runner": str(payload.get("runner", "ubuntu-latest")).strip() or "ubuntu-latest",
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
        update = {
            "ecosystem": ecosystem,
            "directory": directory,
        }
        # Optional Dependabot `versioning-strategy`, validated here so no
        # rendering path can emit a value Dependabot rejects.
        strategy = str(item.get("versioning_strategy", "") or "").strip()
        if strategy:
            validate_versioning_strategy(ecosystem, directory, strategy)
            update["versioning_strategy"] = strategy
        updates.append(update)
    return {
        "enabled": bool(payload.get("enabled", True)),
        "updates": updates,
        "open_pull_requests_limit": int(payload.get("open_pull_requests_limit", 10)),
        "schedule_interval": str(payload.get("schedule_interval", "weekly")).strip() or "weekly",
        "labels": dedupe_strings(payload.get("labels", ["dependencies"])),
    }


#: GitHub's documented values for the repository-ruleset fields a profile may
#: set (REST "Create a repository ruleset"). Anything else would be rejected by
#: the API at apply time, so it is rejected here, while rendering.
RULESET_KEYS = frozenset({"enforcement", "bypass_actors", "code_scanning"})
RULESET_ENFORCEMENTS = frozenset({"active", "disabled", "evaluate"})
RULESET_BYPASS_ACTOR_KEYS = frozenset({"actor_type", "actor_id", "bypass_mode"})
RULESET_BYPASS_ACTOR_TYPES = frozenset(
    {"Integration", "OrganizationAdmin", "RepositoryRole", "Team", "DeployKey", "User"}
)
#: Actor types the API requires an ``actor_id`` for. ``DeployKey`` takes none
#: (null), and ``OrganizationAdmin`` ignores it.
RULESET_BYPASS_ACTOR_TYPES_WITH_ID = frozenset({"Integration", "RepositoryRole", "Team", "User"})
RULESET_BYPASS_MODES = frozenset({"always", "pull_request", "exempt"})
CODE_SCANNING_TOOL_KEYS = frozenset({"tool", "alerts_threshold", "security_alerts_threshold"})
CODE_SCANNING_ALERTS_THRESHOLDS = frozenset({"none", "errors", "errors_and_warnings", "all"})
CODE_SCANNING_SECURITY_ALERTS_THRESHOLDS = frozenset({"none", "critical", "high_or_higher", "medium_or_higher", "all"})


def _require_member(value: str, allowed: frozenset, what: str) -> str:
    """Return ``value`` when it is one of ``allowed``; raise ``ValueError`` otherwise."""
    if value not in allowed:
        raise ValueError(f"unsupported ruleset {what} {value!r}; expected one of {sorted(allowed)}")
    return value


def _ruleset_text(item: Mapping[str, Any], key: str, default: str = "") -> str:
    """Return the stripped string under ``key``, or ``default`` when the key is absent.

    A present value must be a non-blank string: ``str()`` would turn a blank
    YAML value into the literal text ``"None"`` and let it through.
    """
    if key not in item:
        if default:
            return default
        raise ValueError(f"ruleset {key} is required")
    value = item[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"ruleset {key} must be a non-empty string, got {value!r}")
    return value.strip()


def _reject_unknown_keys(item: Mapping[str, Any], allowed: frozenset, where: str) -> None:
    """Raise on a key ``allowed`` does not list: a typo must fail, never vanish."""
    unknown = sorted(str(key) for key in item if key not in allowed)
    if unknown:
        raise ValueError(f"unexpected {where} key(s) {unknown}; expected only {sorted(allowed)}")


def _ruleset_list(payload: Mapping[str, Any], key: str) -> List[Any]:
    """Return the list under ``key``: absent means empty, a blank value is an error.

    A blank ``code_scanning:`` in a repo profile would replace the stack's list
    in the merge and silently drop its merge protection; declaring none takes
    an explicit ``[]``.
    """
    if key not in payload:
        return []
    value = payload[key]
    if not isinstance(value, list):
        raise ValueError(f"ruleset {key} must be a list (use [] to declare none), got {value!r}")
    return value


def _normalize_bypass_actor(item: Any) -> Dict[str, Any]:
    """Validate one ruleset bypass actor against the API's per-type rules."""
    if not isinstance(item, Mapping):
        raise ValueError(f"ruleset bypass_actors entries must be mappings, got {item!r}")
    _reject_unknown_keys(item, RULESET_BYPASS_ACTOR_KEYS, "ruleset bypass actor")
    actor_type = _require_member(_ruleset_text(item, "actor_type"), RULESET_BYPASS_ACTOR_TYPES, "actor_type")
    raw_id = item.get("actor_id")
    if raw_id is not None and (not isinstance(raw_id, int) or isinstance(raw_id, bool)):
        raise ValueError(f"ruleset bypass actor_id must be an integer or null, got {raw_id!r}")
    if raw_id is None and actor_type in RULESET_BYPASS_ACTOR_TYPES_WITH_ID:
        raise ValueError(f"ruleset bypass actor_id is required for actor_type {actor_type!r}")
    if raw_id is not None and actor_type == "DeployKey":
        raise ValueError("ruleset bypass actor_id must be null for actor_type 'DeployKey'")
    if actor_type == "OrganizationAdmin":
        # The API ignores the id today and required exactly 1 before; 1 is
        # valid under both readings, so it is the one canonical value.
        if raw_id not in (None, 1):
            raise ValueError("ruleset bypass actor_id for actor_type 'OrganizationAdmin' must be 1 or omitted")
        raw_id = 1
    bypass_mode = _require_member(_ruleset_text(item, "bypass_mode", "always"), RULESET_BYPASS_MODES, "bypass_mode")
    if bypass_mode == "pull_request" and actor_type == "DeployKey":
        raise ValueError("ruleset bypass_mode 'pull_request' is not applicable to actor_type 'DeployKey'")
    return {"actor_id": raw_id, "actor_type": actor_type, "bypass_mode": bypass_mode}


def _normalize_code_scanning_tool(item: Any) -> Dict[str, str]:
    """Validate one code-scanning merge-protection tool entry."""
    if not isinstance(item, Mapping):
        raise ValueError(f"ruleset code_scanning entries must be mappings, got {item!r}")
    _reject_unknown_keys(item, CODE_SCANNING_TOOL_KEYS, "ruleset code_scanning")
    tool = _ruleset_text(item, "tool")
    alerts = _require_member(
        _ruleset_text(item, "alerts_threshold", "all"), CODE_SCANNING_ALERTS_THRESHOLDS, "alerts_threshold"
    )
    security = _require_member(
        _ruleset_text(item, "security_alerts_threshold", "all"),
        CODE_SCANNING_SECURITY_ALERTS_THRESHOLDS,
        "security_alerts_threshold",
    )
    return {"alerts_threshold": alerts, "security_alerts_threshold": security, "tool": tool}


def normalize_ruleset(raw: Any) -> Dict[str, Any]:
    """Normalize the generated ruleset's enforcement, bypass actors and code-scanning rule.

    Called while normalising every profile (the merged stack + repo profile),
    so a value the GitHub API would reject fails the render instead of a later
    ruleset apply. Strict on purpose: profile shape validation runs on the
    NORMALISED profile, so an unknown key, a blank value or a non-mapping
    dropped here would never be reported anywhere. An absent block or field
    takes its default; a blank one is an error, because in the merge it would
    replace (and silently drop) what the stack declares.
    """
    if not isinstance(raw, Mapping):
        raise ValueError(f"ruleset must be a mapping (omit it to inherit the stack's), got {raw!r}")
    _reject_unknown_keys(raw, RULESET_KEYS, "ruleset")
    enforcement = _require_member(_ruleset_text(raw, "enforcement", "active"), RULESET_ENFORCEMENTS, "enforcement")
    return {
        "enforcement": enforcement,
        "bypass_actors": [_normalize_bypass_actor(item) for item in _ruleset_list(raw, "bypass_actors")],
        "code_scanning": [_normalize_code_scanning_tool(item) for item in _ruleset_list(raw, "code_scanning")],
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
    payload = deepcopy(raw_ratchet or {}) if isinstance(raw_ratchet, Mapping) else {}
    raw_baseline = payload.get("baseline")
    baseline = raw_baseline if isinstance(raw_baseline, Mapping) else {}
    return {
        "baseline": dict(baseline),
        "target_date": str(payload.get("target_date", "")).strip(),
        "escalation_date": str(payload.get("escalation_date", "")).strip(),
        "on_escalation": str(payload.get("on_escalation", "absolute")).strip() or "absolute",
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
