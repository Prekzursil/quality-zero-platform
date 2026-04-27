"""Validate normalized control-plane profile contracts."""

from __future__ import absolute_import

from typing import Any, Dict, List, Set, Tuple, Union

from scripts.quality.common import dedupe_strings
from scripts.quality.profile_shape import validate_profile_shape
from scripts.security_helpers import normalize_https_url


def _require_values(slug: str, required_values: List[Tuple[str, Any]]) -> List[str]:
    """Return findings for required profile fields that are empty."""
    return [
        f"{slug}: {field_name} is required"
        for field_name, value in required_values
        if not value
    ]


def _require_expected_values(
    slug: str,
    expected_values: List[Tuple[str, Any, Any]],
) -> List[str]:
    """Return findings for profile fields that differ from expected values."""
    return [
        f"{slug}: {field_name} must be {expected}"
        for field_name, actual, expected in expected_values
        if actual != expected
    ]


def _validate_codex_environment(
    profile: Dict[str, Any],
    codex_environment: Dict[str, Any],
) -> List[str]:
    """Validate the codex runner contract required by the control plane."""
    slug = profile["slug"]
    findings = _require_expected_values(
        slug,
        [
            ("codex_environment.mode", codex_environment.get("mode"), "automatic"),
            (
                "codex_environment.network_profile",
                codex_environment.get("network_profile"),
                "unrestricted",
            ),
            ("codex_environment.methods", codex_environment.get("methods"), "all"),
        ],
    )
    findings.extend(
        _require_values(
            slug,
            [
                (
                    "codex_environment.verify_command",
                    codex_environment.get("verify_command"),
                ),
                ("codex_environment.auth_file", codex_environment.get("auth_file")),
                (
                    "codex_environment.runner_labels",
                    codex_environment.get("runner_labels", []),
                ),
            ],
        )
    )
    runner_labels = codex_environment.get("runner_labels", [])
    if runner_labels and "self-hosted" not in runner_labels:
        findings.append(
            f"{slug}: codex_environment.runner_labels must include self-hosted"
        )
    return findings


def _validate_control_plane_lanes(
    profile: Dict[str, Any],
) -> List[str]:
    """Validate core control-plane lane defaults for one profile."""
    slug = profile["slug"]
    findings = _require_values(
        slug,
        [("verify_command", profile.get("verify_command"))],
    )
    findings.extend(
        _require_expected_values(
            slug,
            [
                (
                    "github_mutation_lane",
                    profile.get("github_mutation_lane"),
                    "codex-private-runner",
                ),
                ("codex_auth_lane", profile.get("codex_auth_lane"), "chatgpt-account"),
                (
                    "provider_ui_mode",
                    profile.get("provider_ui_mode"),
                    "playwright-manual-login",
                ),
            ],
        )
    )
    findings.extend(
        _validate_codex_environment(
            profile,
            profile.get("codex_environment", {}),
        )
    )
    return findings


def _missing_required_now_contexts(profile: Dict[str, Any]) -> List[str]:
    """Return always and PR-only contexts missing from required_now."""
    pr_context_defaults = dedupe_strings(
        [
            *profile["required_contexts"].get("always", []),
            *profile["required_contexts"].get("pull_request_only", []),
        ]
    )
    required_now = set(profile["required_contexts"].get("required_now", []))
    return [
        name
        for name in pr_context_defaults
        if not _contains_required_context(required_now, name)
    ]


def _missing_target_contexts(profile: Dict[str, Any]) -> List[str]:
    """Return required-now contexts that are absent from the target set."""
    required_now = set(profile["required_contexts"].get("required_now", []))
    target_contexts = set(profile["required_contexts"].get("target", []))
    return [name for name in required_now if name not in target_contexts]


def _missing_ruleset_target_contexts(
    profile: Dict[str, Any],
    *,
    ruleset_contexts: Set[str],
) -> List[str]:
    """Return target contexts that are not emitted by the ruleset contract."""
    target_contexts = set(profile["required_contexts"].get("target", []))
    return [
        name
        for name in target_contexts
        if not _contains_required_context(ruleset_contexts, name)
    ]


def _matches_required_context(actual_context: str, expected_context: str) -> bool:
    """Return whether one emitted status context satisfies the expected name."""
    current = str(actual_context or "").strip()
    expected = str(expected_context or "").strip()
    return bool(current) and bool(expected) and (
        expected in (current, current.rsplit(" / ", 1)[-1])
    )


def _contains_required_context(
    contexts: List[str] | Set[str],
    expected_context: str,
) -> bool:
    """Return whether any available context matches the expected status check."""
    return any(
        _matches_required_context(actual_context, expected_context)
        for actual_context in contexts
    )


def _validate_required_context_sets(
    profile: Dict[str, Any],
    *,
    active_required_contexts_fn,
) -> List[str]:
    """Validate the relationship between always, target, and required-now checks."""
    findings: List[str] = []
    ruleset_contexts = set(active_required_contexts_fn(profile, event_name="ruleset"))
    if not ruleset_contexts:
        findings.append(f"{profile['slug']}: at least one required context is required")
    missing_required_now = _missing_required_now_contexts(profile)
    if missing_required_now:
        findings.append(
            f"{profile['slug']}: required_contexts.required_now is missing "
            f"{', '.join(missing_required_now)}"
        )
    missing_target = _missing_target_contexts(profile)
    if missing_target:
        findings.append(
            f"{profile['slug']}: required_contexts.target is missing "
            f"{', '.join(missing_target)}"
        )
    missing_ruleset_target = _missing_ruleset_target_contexts(
        profile,
        ruleset_contexts=ruleset_contexts,
    )
    if missing_ruleset_target:
        findings.append(
            f"{profile['slug']}: emitted ruleset contexts are missing "
            f"{', '.join(missing_ruleset_target)}"
        )
    return findings


def _validate_secret_contract(profile: Dict[str, Any]) -> List[str]:
    """Validate required and conditional secret declarations."""
    findings: List[str] = []
    duplicate_conditional = [
        name
        for name in profile.get("conditional_secrets", [])
        if name in profile.get("required_secrets", [])
    ]
    if duplicate_conditional:
        findings.append(
            f"{profile['slug']}: conditional_secrets duplicates required_secrets "
            f"for {', '.join(duplicate_conditional)}"
        )
    findings.extend(
        f"{profile['slug']}: {secret_name} must not be part of required_secrets"
        for secret_name in ["OPENAI_API_KEY"]
        if secret_name in profile.get("required_secrets", [])
    )
    return findings


def _validate_issue_policy_contract(profile: Dict[str, Any]) -> List[str]:
    """Validate the issue policy block for one repository profile."""
    issue_policy = profile.get("issue_policy", {})
    findings: List[str] = []
    if issue_policy.get("mode") not in {"zero", "ratchet", "audit"}:
        findings.append(
            f"{profile['slug']}: issue_policy.mode must be zero, ratchet, "
            f"or audit"
        )
    if issue_policy.get("pr_behavior") not in {"introduced_only", "absolute"}:
        findings.append(
            f"{profile['slug']}: issue_policy.pr_behavior must be "
            f"introduced_only or absolute"
        )
    if issue_policy.get("main_behavior") != "absolute":
        findings.append(
            f"{profile['slug']}: issue_policy.main_behavior must be absolute"
        )
    if issue_policy.get("mode") == "ratchet" and not issue_policy.get("baseline_ref"):
        findings.append(
            f"{profile['slug']}: issue_policy.baseline_ref is required "
            f"when mode is ratchet"
        )
    return findings


def _validate_deps_contract(profile: Dict[str, Any]) -> List[str]:
    """Validate dependency-alert policy settings for one profile."""
    deps = profile.get("deps", {})
    findings: List[str] = []
    if deps.get("policy") not in {"zero_critical", "zero_high", "zero_any"}:
        findings.append(
            f"{profile['slug']}: deps.policy must be zero_critical, "
            f"zero_high, or zero_any"
        )
    if deps.get("scope") not in {"runtime", "all"}:
        findings.append(f"{profile['slug']}: deps.scope must be runtime or all")
    return findings


def _validate_visual_pair_contract(
    profile: Dict[str, Any],
    *,
    active_required_contexts_fn,
) -> List[str]:
    """Validate repositories that require paired visual-review providers."""
    if not profile.get("visual_pair_required"):
        return []

    findings: List[str] = []
    vendors = profile["vendors"]
    chromatic = vendors["chromatic"]["status_context"]
    applitools = vendors["applitools"]["status_context"]
    ruleset_contexts = set(active_required_contexts_fn(profile, event_name="ruleset"))
    target_contexts = set(profile["required_contexts"].get("target", []))
    paired_contexts = [
        (ruleset_contexts, "ruleset"),
        (target_contexts, "target"),
    ]
    findings.extend(
        " ".join(
            [
                f"{profile['slug']}: visual_pair_required needs both Chromatic",
                f"and Applitools contexts in {label}",
            ]
        )
        for contexts, label in paired_contexts
        if (chromatic in contexts) != (applitools in contexts)
    )
    findings.extend(
        f"{profile['slug']}: visual_pair_required requires {vendor_name}.{field_name}"
        for vendor_name, field_name in [
            ("chromatic", "project_name"),
            ("chromatic", "token_secret"),
            ("chromatic", "local_env_var"),
            ("applitools", "project_name"),
        ]
        if not vendors[vendor_name].get(field_name)
    )
    return findings


def _validate_coverage_contract(profile: Dict[str, Any]) -> List[str]:
    """Validate coverage collection and assertion settings for one profile."""
    coverage = profile.get("coverage", {})
    if not profile.get("enabled_scanners", {}).get("coverage", False):
        return []

    findings = _require_values(
        profile["slug"],
        [("coverage.command", coverage.get("command"))],
    )
    if not coverage.get("inputs"):
        findings.append(
            f"{profile['slug']}: coverage.inputs must declare at least one report"
        )
    if coverage.get("shell") not in {"bash", "pwsh"}:
        findings.append(f"{profile['slug']}: coverage.shell must be bash or pwsh")
    findings.extend(
        " ".join(
            [
                f"{profile['slug']}: coverage.assert_mode.{mode_name} must be",
                "enforce, evidence_only, or non_regression",
            ]
        )
        for mode_name, mode_value in coverage.get("assert_mode", {}).items()
        if mode_value not in {"enforce", "evidence_only", "non_regression"}
    )
    if coverage.get("require_sources_mode") not in {"explicit", "infer", "disabled"}:
        findings.append(
            f"{profile['slug']}: coverage.require_sources_mode must be "
            f"explicit, infer, or disabled"
        )
    return findings


def _validate_vendor_urls(profile: Dict[str, Any]) -> List[str]:
    """Validate all vendor URL fields after profile normalization."""
    findings: List[str] = []
    for vendor_name, vendor_payload in profile["vendors"].items():
        if not isinstance(vendor_payload, dict):
            continue
        for key, value in vendor_payload.items():
            if key.endswith("_url") and value:
                try:
                    normalize_https_url(str(value))
                except ValueError as exc:
                    findings.append(
                        f"{profile['slug']}: invalid {vendor_name}.{key}: {exc}"
                    )
    return findings


def validate_profile(
    profile: Dict[str, Any],
    *,
    active_required_contexts_fn,
) -> List[str]:
    """Run every profile-contract validator and return the combined findings."""
    findings: List[str] = validate_profile_shape(profile, slug=profile["slug"])
    validators = (
        _validate_control_plane_lanes,
        lambda current: _validate_required_context_sets(
            current,
            active_required_contexts_fn=active_required_contexts_fn,
        ),
        _validate_secret_contract,
        _validate_issue_policy_contract,
        _validate_deps_contract,
        lambda current: _validate_visual_pair_contract(
            current,
            active_required_contexts_fn=active_required_contexts_fn,
        ),
        _validate_coverage_contract,
        _validate_vendor_urls,
    )
    for validator in validators:
        findings.extend(validator(profile))
    return findings
