"""Profile shape."""

from __future__ import absolute_import

from typing import Any, Dict, List, Mapping, Set

TOP_LEVEL_KEYS: Set[str] = {
    "slug",
    "stack",
    "id",
    "default_branch",
    "verify_command",
    "github_mutation_lane",
    "codex_auth_lane",
    "provider_ui_mode",
    "codex_environment",
    "required_secrets",
    "conditional_secrets",
    "required_vars",
    "enabled_scanners",
    "issue_policy",
    "deps",
    "required_contexts",
    "coverage",
    "ruleset_mode",
    "preserve_public_check_names",
    "vendors",
    "providers",
    "codeql",
    "dependabot",
    "trigger",
    "visual_pair_required",
    "visual_lane",
    "visual_regression",
    "legacy_policy_checks",
    "required_contexts_mode",
    "stack_family",
    "rollout",
    "rollout_notes",
    "profile_id",
    "repo_name",
    "owner",
    # v2 schema extensions (see docs/QZP-V2-DESIGN.md §3).
    # `version: 2` marks a profile as opting into the v2 contract; absence
    # means v1 (existing behaviour, no breaking change). v1 profiles remain
    # fully valid; v2 introduces `mode`, `scanners`, and `overrides` as the
    # structured replacements for `issue_policy` and `enabled_scanners`.
    "version",
    "mode",
    "scanners",
    "overrides",
}
NESTED_KEYS: Dict[str, Set[str]] = {
    "codex_environment": {
        "mode",
        "verify_command",
        "auth_file",
        "network_profile",
        "methods",
        "runner_labels",
    },
    "required_contexts": {"always", "pull_request_only", "required_now", "target"},
    "issue_policy": {"mode", "pr_behavior", "main_behavior", "baseline_ref"},
    "deps": {"enabled", "policy", "scope"},
    "coverage": {
        "runner",
        "shell",
        "command_shell",
        "command",
        "inputs",
        "artifact_path",
        "mode",
        "require_sources",
        "require_sources_mode",
        "min_percent",
        "branch_min_percent",
        "assert_mode",
        "evidence_note",
        "setup",
        "policy",
    },
    "trigger": {"mode", "pr_head_sha"},
    "visual_lane": {"kind"},
    "codeql": {"enabled", "languages", "runner", "build_mode", "setup"},
    "dependabot": {
        "enabled",
        "updates",
        "open_pull_requests_limit",
        "schedule_interval",
        "labels",
    },
    # v2: governance mode declares the phase the repo is in plus optional
    # ratcheting details. `phase` must be one of {shadow, ratchet, absolute}.
    "mode": {"phase", "shadow_until", "ratchet"},
}


def validate_profile_shape(profile: Mapping[str, Any], *, slug: str) -> List[str]:
    """Report unexpected top-level and nested profile keys."""
    findings: List[str] = []
    unexpected = sorted(set(profile) - TOP_LEVEL_KEYS)
    findings.extend(f"{slug}: unexpected profile key `{key}`" for key in unexpected)

    for section_name, allowed_keys in NESTED_KEYS.items():
        section = profile.get(section_name)
        if not isinstance(section, dict):
            continue
        extra = sorted(set(section) - allowed_keys)
        findings.extend(
            f"{slug}: unexpected {section_name} key `{key}`" for key in extra
        )

    return findings
