"""Normalization helpers for quality profile configuration."""

from __future__ import absolute_import

from copy import deepcopy
from typing import Any, Dict, List, Mapping

from scripts.quality.common import dedupe_strings
from scripts.quality import profile_coverage_normalization


def _issue_policy_defaults(mode: str) -> Dict[str, str]:
    """Return default issue-policy values for the selected mode."""
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
    """Merge explicit issue-policy fields over the mode defaults."""
    defaults = _issue_policy_defaults(mode)
    return {
        "mode": mode,
        "pr_behavior": (
            str(payload.get("pr_behavior", defaults["pr_behavior"])).strip()
            or defaults["pr_behavior"]
        ),
        "main_behavior": (
            str(payload.get("main_behavior", defaults["main_behavior"])).strip()
            or defaults["main_behavior"]
        ),
        "baseline_ref": (
            str(payload.get("baseline_ref", defaults["baseline_ref"])).strip()
            or defaults["baseline_ref"]
        ),
    }


def normalize_issue_policy(
    raw_issue_policy: Mapping[str, Any] | str | None,
) -> Dict[str, str]:
    """Normalize issue policy configuration."""
    if isinstance(raw_issue_policy, str):
        return _issue_policy_defaults(str(raw_issue_policy or "").strip() or "ratchet")

    payload = (
        deepcopy(raw_issue_policy or {})
        if isinstance(raw_issue_policy, dict)
        else {}
    )
    mode = str(payload.get("mode", "ratchet")).strip() or "ratchet"
    return _merge_issue_policy_defaults(mode, payload)


def normalize_deps(raw_deps: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Normalize dependency policy configuration."""
    payload = deepcopy(raw_deps or {}) if isinstance(raw_deps, dict) else {}
    return {
        "enabled": bool(payload.get("enabled", False)),
        "policy": (
            str(payload.get("policy", "zero_critical")).strip() or "zero_critical"
        ),
        "scope": str(payload.get("scope", "runtime")).strip() or "runtime",
    }


def normalize_required_contexts(
    raw: Mapping[str, Any] | None,
) -> Dict[str, List[str]]:
    """Normalize required status contexts."""
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
    base: Mapping[str, Any] | None,
    overlay: Mapping[str, Any] | None,
) -> Dict[str, List[str]]:
    """Merge two required-context payloads."""
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


def normalize_coverage_inputs(raw_inputs: Any) -> List[Dict[str, str]]:
    """Normalize coverage input declarations."""
    return profile_coverage_normalization.normalize_coverage_inputs(raw_inputs)


def infer_coverage_inputs(coverage: Mapping[str, Any] | None) -> List[Dict[str, str]]:
    """Infer coverage inputs from coverage config."""
    return profile_coverage_normalization.infer_coverage_inputs(coverage)


def infer_required_sources(raw_coverage: Mapping[str, Any] | None) -> List[str]:
    """Infer required source paths from coverage data."""
    return profile_coverage_normalization.infer_required_sources(raw_coverage)


def normalize_java_setup(raw_java: Any) -> Dict[str, Any]:
    """Normalize Java setup configuration."""
    return profile_coverage_normalization.normalize_java_setup(raw_java)


def normalize_coverage_setup(raw_setup: Any) -> Dict[str, Any]:
    """Normalize coverage setup configuration."""
    return profile_coverage_normalization.normalize_coverage_setup(raw_setup)


def normalize_coverage_assert_mode(raw_assert_mode: Any) -> Dict[str, str]:
    """Normalize coverage assert-mode configuration."""
    return profile_coverage_normalization.normalize_coverage_assert_mode(
        raw_assert_mode
    )


def normalize_coverage(raw: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Normalize coverage configuration."""
    return profile_coverage_normalization.normalize_coverage(raw)


def normalize_codex_environment(
    raw: Mapping[str, Any] | None,
    *,
    verify_command: str,
) -> Dict[str, Any]:
    """Normalize Codex environment configuration."""
    payload = deepcopy(raw or {}) if isinstance(raw, dict) else {}
    return {
        "mode": str(payload.get("mode", "automatic")).strip() or "automatic",
        "verify_command": (
            str(payload.get("verify_command", verify_command)).strip() or verify_command
        ),
        "auth_file": (
            str(payload.get("auth_file", "~/.codex/auth.json")).strip()
            or "~/.codex/auth.json"
        ),
        "network_profile": (
            str(payload.get("network_profile", "unrestricted")).strip()
            or "unrestricted"
        ),
        "methods": str(payload.get("methods", "all")).strip() or "all",
        "runner_labels": dedupe_strings(
            payload.get("runner_labels", ["self-hosted", "codex-trusted"])
        ),
    }
