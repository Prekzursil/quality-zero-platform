from __future__ import absolute_import

from copy import deepcopy
from typing import Any, Dict, List, Mapping

from scripts.quality.common import dedupe_strings
from scripts.quality.profile_coverage_normalization import (
    infer_coverage_inputs,
    infer_required_sources,
    normalize_coverage,
    normalize_coverage_assert_mode,
    normalize_coverage_inputs,
    normalize_coverage_setup,
    normalize_java_setup,
)


def normalize_issue_policy(raw_issue_policy: Mapping[str, Any] | str | None) -> Dict[str, str]:
    if isinstance(raw_issue_policy, str):
        mode = str(raw_issue_policy or "").strip() or "ratchet"
        return {
            "mode": mode,
            "pr_behavior": "absolute" if mode == "zero" else "introduced_only",
            "main_behavior": "absolute",
            "baseline_ref": "" if mode == "zero" else "main",
        }

    payload = deepcopy(raw_issue_policy or {}) if isinstance(raw_issue_policy, dict) else {}
    mode = str(payload.get("mode", "ratchet")).strip() or "ratchet"
    pr_behavior = str(
        payload.get("pr_behavior", "absolute" if mode == "zero" else "introduced_only")
    ).strip() or ("absolute" if mode == "zero" else "introduced_only")
    main_behavior = str(payload.get("main_behavior", "absolute")).strip() or "absolute"
    return {
        "mode": mode,
        "pr_behavior": pr_behavior,
        "main_behavior": main_behavior,
        "baseline_ref": str(payload.get("baseline_ref", "" if mode == "zero" else "main")).strip() or ("" if mode == "zero" else "main"),
    }


def normalize_deps(raw_deps: Mapping[str, Any] | None) -> Dict[str, Any]:
    payload = deepcopy(raw_deps or {}) if isinstance(raw_deps, dict) else {}
    return {
        "enabled": bool(payload.get("enabled", False)),
        "policy": str(payload.get("policy", "zero_critical")).strip() or "zero_critical",
        "scope": str(payload.get("scope", "runtime")).strip() or "runtime",
    }


def normalize_required_contexts(raw: Mapping[str, Any] | None) -> Dict[str, List[str]]:
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
    base_payload = base if isinstance(base, Mapping) else {}
    overlay_payload = overlay if isinstance(overlay, Mapping) else {}
    return normalize_required_contexts(
        {
            "always": [*base_payload.get("always", []), *overlay_payload.get("always", [])],
            "pull_request_only": [*base_payload.get("pull_request_only", []), *overlay_payload.get("pull_request_only", [])],
            "required_now": [*base_payload.get("required_now", []), *overlay_payload.get("required_now", [])],
            "target": [*base_payload.get("target", []), *overlay_payload.get("target", [])],
        }
    )


def normalize_codex_environment(raw: Mapping[str, Any] | None, *, verify_command: str) -> Dict[str, Any]:
    payload = deepcopy(raw or {}) if isinstance(raw, dict) else {}
    return {
        "mode": str(payload.get("mode", "automatic")).strip() or "automatic",
        "verify_command": str(payload.get("verify_command", verify_command)).strip() or verify_command,
        "auth_file": str(payload.get("auth_file", "~/.codex/auth.json")).strip() or "~/.codex/auth.json",
        "network_profile": str(payload.get("network_profile", "unrestricted")).strip() or "unrestricted",
        "methods": str(payload.get("methods", "all")).strip() or "all",
        "runner_labels": dedupe_strings(payload.get("runner_labels", ["self-hosted", "codex-trusted"])),
    }
