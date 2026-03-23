from __future__ import absolute_import

import argparse
import json
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple, cast

import yaml  # type: ignore[import-untyped]

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import (
    _deep_merge as common_deep_merge,
    dedupe_strings,
)
from scripts.quality.control_plane_vendors import finalize_vendors, normalize_visual_lane
from scripts.quality.profile_shape import validate_profile_shape
from scripts.quality.profile_normalization import (
    normalize_deps as common_normalize_deps,
    infer_coverage_inputs as common_infer_coverage_inputs,
    merge_required_contexts as common_merge_required_contexts,
    normalize_codex_environment as common_normalize_codex_environment,
    normalize_coverage as common_normalize_coverage,
    normalize_coverage_assert_mode as common_normalize_coverage_assert_mode,
    normalize_coverage_inputs as common_normalize_coverage_inputs,
    normalize_issue_policy as common_normalize_issue_policy,
    normalize_java_setup as common_normalize_java_setup,
    normalize_required_contexts as common_normalize_required_contexts,
)
from scripts.security_helpers import normalize_https_url


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY_PATH = ROOT / "inventory" / "repos.yml"


@dataclass(frozen=True)
class RepoSources:
    repo_entry: Dict[str, Any]
    profile_id: str
    repo_profile: Dict[str, Any]
    stack_id: str
    stack_profile: Dict[str, Any]


@dataclass(frozen=True)
class InventoryOverrides:
    repo_entry: Dict[str, Any]
    repo_slug: str
    profile_id: str
    stack_id: str
    required_contexts_mode: str


def repo_root() -> Path:
    return ROOT


def _load_yaml(path: Path) -> Dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected mapping at {path}")
    return payload


def _deep_merge(base: Any, overlay: Any) -> Any:
    return common_deep_merge(base, overlay)


def _inventory_root(inventory: Dict[str, Any]) -> Path:
    return Path(inventory["_root"])


def load_inventory(path: Path | str = DEFAULT_INVENTORY_PATH) -> Dict[str, Any]:
    inventory_path = Path(path).resolve()
    payload = _load_yaml(inventory_path)
    payload.setdefault("version", 1)
    payload.setdefault("repos", [])
    if not isinstance(payload["repos"], list):
        raise ValueError("inventory repos must be a list")
    payload["_inventory_path"] = str(inventory_path)
    payload["_root"] = str(inventory_path.parent.parent)
    return payload


def _load_stack(inventory: Dict[str, Any], stack_id: str, seen: Set[str] | None = None) -> Dict[str, Any]:
    if not stack_id:
        raise ValueError("stack id is required")
    trail = set(seen or set())
    if stack_id in trail:
        raise ValueError(f"Stack inheritance cycle detected at {stack_id}")
    trail.add(stack_id)

    stack_path = _inventory_root(inventory) / "profiles" / "stacks" / f"{stack_id}.yml"
    payload = _load_yaml(stack_path)
    parents = payload.pop("extends", [])
    if isinstance(parents, str):
        parents = [parents]

    merged: Dict[str, Any] = {}
    for parent in parents:
        merged = _deep_merge(merged, _load_stack(inventory, str(parent), seen=trail))
    merged = _deep_merge(merged, payload)
    merged.setdefault("id", stack_id)
    return merged


def _normalize_required_contexts(raw: Dict[str, Any]) -> Dict[str, List[str]]:
    return common_normalize_required_contexts(raw)


def _merge_required_contexts(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, List[str]]:
    return common_merge_required_contexts(base, overlay)


def _normalize_coverage_inputs(raw_inputs: Any) -> List[Dict[str, str]]:
    return common_normalize_coverage_inputs(raw_inputs)


def _infer_coverage_inputs(coverage: Dict[str, Any]) -> List[Dict[str, str]]:
    return common_infer_coverage_inputs(coverage)


def _normalize_java_setup(raw_java: Any) -> Dict[str, Any]:
    return common_normalize_java_setup(raw_java)


def _normalize_coverage_setup(raw_setup: Any) -> Dict[str, Any]:
    coverage = common_normalize_coverage({"setup": raw_setup})
    return cast(Dict[str, Any], coverage["setup"])


def _normalize_coverage_assert_mode(raw_assert_mode: Any) -> Dict[str, str]:
    return common_normalize_coverage_assert_mode(raw_assert_mode)


def _normalize_coverage(raw: Dict[str, Any]) -> Dict[str, Any]:
    return common_normalize_coverage(raw)


def _normalize_codex_environment(raw: Dict[str, Any], *, verify_command: str) -> Dict[str, Any]:
    return common_normalize_codex_environment(raw, verify_command=verify_command)


def _normalize_issue_policy(raw: Dict[str, Any]) -> Dict[str, str]:
    return common_normalize_issue_policy(raw)



def _resolve_repo_sources(inventory: Dict[str, Any], repo_slug: str) -> RepoSources:
    repo_entry = next((item for item in inventory["repos"] if item.get("slug") == repo_slug), None)
    if repo_entry is None:
        raise KeyError(f"Repo {repo_slug} not found in inventory")

    profile_id = str(repo_entry.get("profile", "")).strip()
    if not profile_id:
        raise ValueError(f"Repo {repo_slug} is missing a profile id")

    profile_path = _inventory_root(inventory) / "profiles" / "repos" / f"{profile_id}.yml"
    repo_profile = _load_yaml(profile_path)
    stack_id = str(repo_profile.get("stack", "")).strip()
    stack_profile = _load_stack(inventory, stack_id)
    return RepoSources(repo_entry, profile_id, repo_profile, stack_id, stack_profile)


def _merge_repo_profile(stack_profile: Dict[str, Any], repo_profile: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    merged = _deep_merge(stack_profile, repo_profile)
    required_contexts_mode = str(repo_profile.get("required_contexts_mode", "merge")).strip() or "merge"
    if required_contexts_mode == "replace":
        merged["required_contexts"] = common_normalize_required_contexts(repo_profile.get("required_contexts", {}))
    else:
        merged["required_contexts"] = common_merge_required_contexts(
            stack_profile.get("required_contexts", {}),
            repo_profile.get("required_contexts", {}),
        )
    merged["required_secrets"] = dedupe_strings(
        [*stack_profile.get("required_secrets", []), *repo_profile.get("required_secrets", [])]
    )
    merged["conditional_secrets"] = dedupe_strings(
        [*stack_profile.get("conditional_secrets", []), *repo_profile.get("conditional_secrets", [])]
    )
    merged["required_vars"] = dedupe_strings([*stack_profile.get("required_vars", []), *repo_profile.get("required_vars", [])])
    merged["providers"] = _deep_merge(stack_profile.get("providers", {}), repo_profile.get("providers", {}))
    merged["vendors"] = _deep_merge(stack_profile.get("vendors", {}), repo_profile.get("vendors", {}))
    return merged, required_contexts_mode


def _apply_inventory_overrides(merged: Dict[str, Any], overrides: InventoryOverrides | Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(overrides, InventoryOverrides):
        overrides = InventoryOverrides(
            repo_entry=cast(Dict[str, Any], overrides["repo_entry"]),
            repo_slug=str(overrides["repo_slug"]),
            profile_id=str(overrides["profile_id"]),
            stack_id=str(overrides["stack_id"]),
            required_contexts_mode=str(overrides["required_contexts_mode"]),
        )

    return _deep_merge(
        merged,
        {
            "slug": overrides.repo_slug,
            "default_branch": overrides.repo_entry.get("default_branch", merged.get("default_branch", "main")),
            "rollout": overrides.repo_entry.get("rollout", merged.get("rollout", "")),
            "rollout_notes": overrides.repo_entry.get("notes", ""),
            "profile_id": overrides.profile_id,
            "stack": overrides.stack_id,
            "required_contexts_mode": overrides.required_contexts_mode,
        },
    )


def _finalize_repo_profile(merged: Dict[str, Any], repo_slug: str) -> Dict[str, Any]:
    owner, repo_name = repo_slug.split("/", 1)
    merged["owner"] = owner
    merged["repo_name"] = repo_name
    merged["verify_command"] = str(merged.get("verify_command", "bash scripts/verify")).strip()
    merged["github_mutation_lane"] = (
        str(merged.get("github_mutation_lane", "codex-private-runner")).strip() or "codex-private-runner"
    )
    merged["codex_auth_lane"] = str(merged.get("codex_auth_lane", "chatgpt-account")).strip() or "chatgpt-account"
    merged["provider_ui_mode"] = str(merged.get("provider_ui_mode", "playwright-manual-login")).strip() or "playwright-manual-login"
    merged["required_secrets"] = dedupe_strings(merged.get("required_secrets", []))
    merged["conditional_secrets"] = dedupe_strings(merged.get("conditional_secrets", []))
    merged["required_vars"] = dedupe_strings(merged.get("required_vars", []))
    merged["required_contexts"] = common_normalize_required_contexts(merged.get("required_contexts", {}))
    merged["enabled_scanners"] = merged.get("enabled_scanners", {})
    merged["issue_policy"] = common_normalize_issue_policy(merged.get("issue_policy", {}))
    merged["deps"] = common_normalize_deps(merged.get("deps", {}))
    merged["enabled_scanners"]["deps"] = bool(merged["deps"]["enabled"])
    merged["coverage"] = common_normalize_coverage(merged.get("coverage", {}))
    merged["codex_environment"] = common_normalize_codex_environment(
        merged.get("codex_environment", {}),
        verify_command=merged["verify_command"],
    )
    merged["visual_pair_required"] = bool(merged.get("visual_pair_required", False))
    merged["visual_lane"] = normalize_visual_lane(merged.get("visual_lane", {}))
    merged["ruleset_mode"] = str(merged.get("ruleset_mode", "strict-zero-phase1")).strip()
    merged["preserve_public_check_names"] = bool(merged.get("preserve_public_check_names", True))
    vendor_source = _deep_merge(merged.get("vendors", {}), merged.get("providers", {}))
    merged["vendors"] = finalize_vendors(merged, vendor_source)
    merged["providers"] = deepcopy(merged["vendors"])
    return merged


def load_repo_profile(inventory: Dict[str, Any], repo_slug: str) -> Dict[str, Any]:
    repo_sources = _resolve_repo_sources(inventory, repo_slug)
    repo_entry = repo_sources.repo_entry
    profile_id = repo_sources.profile_id
    repo_profile = repo_sources.repo_profile
    stack_id = repo_sources.stack_id
    stack_profile = repo_sources.stack_profile
    merged, required_contexts_mode = _merge_repo_profile(stack_profile, repo_profile)
    merged = _apply_inventory_overrides(
        merged,
        InventoryOverrides(
            repo_entry=repo_entry,
            repo_slug=repo_slug,
            profile_id=profile_id,
            stack_id=stack_id,
            required_contexts_mode=required_contexts_mode,
        ),
    )
    return _finalize_repo_profile(merged, repo_slug)


def active_required_contexts(profile: Dict[str, Any], *, event_name: str) -> List[str]:
    required_contexts = profile["required_contexts"]
    if event_name == "ruleset":
        return dedupe_strings(required_contexts.get("required_now", []))

    required = list(required_contexts["always"])
    if event_name in {"pull_request", "pull_request_target"}:
        required.extend(required_contexts["pull_request_only"])
    return dedupe_strings(required)


def build_ruleset_payload(profile: Dict[str, Any]) -> Dict[str, Any]:
    contexts = active_required_contexts(profile, event_name="ruleset")
    return {
        "profile_id": profile["profile_id"],
        "repo_slug": profile["slug"],
        "name": f"quality-zero-platform / {profile['repo_name']}",
        "target": "branch",
        "enforcement": "active",
        "conditions": {
            "ref_name": {
                "include": [f"refs/heads/{profile['default_branch']}"],
                "exclude": [],
            }
        },
        "bypass_actors": [],
        "rules": [
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 1,
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": False,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [{"context": name, "integration_id": None} for name in contexts],
                },
            },
            {"type": "non_fast_forward"},
            {"type": "deletion"},
        ],
    }


def _require_values(slug: str, required_values: List[Tuple[str, Any]]) -> List[str]:
    return [f"{slug}: {field_name} is required" for field_name, value in required_values if not value]


def _require_expected_values(slug: str, expected_values: List[Tuple[str, Any, Any]]) -> List[str]:
    return [f"{slug}: {field_name} must be {expected}" for field_name, actual, expected in expected_values if actual != expected]


def _validate_codex_environment(profile: Dict[str, Any], codex_environment: Dict[str, Any]) -> List[str]:
    slug = profile["slug"]
    findings = _require_expected_values(
        slug,
        [
            ("codex_environment.mode", codex_environment.get("mode"), "automatic"),
            ("codex_environment.network_profile", codex_environment.get("network_profile"), "unrestricted"),
            ("codex_environment.methods", codex_environment.get("methods"), "all"),
        ],
    )
    findings.extend(
        _require_values(
            slug,
            [
                ("codex_environment.verify_command", codex_environment.get("verify_command")),
                ("codex_environment.auth_file", codex_environment.get("auth_file")),
                ("codex_environment.runner_labels", codex_environment.get("runner_labels", [])),
            ],
        )
    )
    runner_labels = codex_environment.get("runner_labels", [])
    if runner_labels and "self-hosted" not in runner_labels:
        findings.append(f"{slug}: codex_environment.runner_labels must include self-hosted")
    return findings


def _validate_control_plane_lanes(profile: Dict[str, Any]) -> List[str]:
    slug = profile["slug"]
    findings = _require_values(slug, [("verify_command", profile.get("verify_command"))])
    findings.extend(
        _require_expected_values(
            slug,
            [
                ("github_mutation_lane", profile.get("github_mutation_lane"), "codex-private-runner"),
                ("codex_auth_lane", profile.get("codex_auth_lane"), "chatgpt-account"),
                ("provider_ui_mode", profile.get("provider_ui_mode"), "playwright-manual-login"),
            ],
        )
    )
    findings.extend(_validate_codex_environment(profile, profile.get("codex_environment", {})))
    return findings


def _validate_required_context_sets(profile: Dict[str, Any]) -> List[str]:
    findings: List[str] = []
    if not active_required_contexts(profile, event_name="ruleset"):
        findings.append(f"{profile['slug']}: at least one required context is required")
    pr_contexts = dedupe_strings(
        [*profile["required_contexts"].get("always", []), *profile["required_contexts"].get("pull_request_only", [])]
    )
    required_now = set(profile["required_contexts"].get("required_now", []))
    missing_required_now = [name for name in pr_contexts if name not in required_now]
    if missing_required_now:
        findings.append(
            f"{profile['slug']}: required_contexts.required_now is missing {', '.join(missing_required_now)}"
        )
    target_contexts = set(profile["required_contexts"].get("target", []))
    missing_target = [name for name in required_now if name not in target_contexts]
    if missing_target:
        findings.append(
            f"{profile['slug']}: required_contexts.target is missing {', '.join(missing_target)}"
        )
    return findings


def _validate_secret_contract(profile: Dict[str, Any]) -> List[str]:
    findings: List[str] = []
    duplicate_conditional = [name for name in profile.get("conditional_secrets", []) if name in profile.get("required_secrets", [])]
    if duplicate_conditional:
        findings.append(
            f"{profile['slug']}: conditional_secrets duplicates required_secrets for {', '.join(duplicate_conditional)}"
        )
    findings.extend(
        f"{profile['slug']}: {secret_name} must not be part of required_secrets"
        for secret_name in ["OPENAI_API_KEY"]
        if secret_name in profile.get("required_secrets", [])
    )
    return findings


def _validate_issue_policy_contract(profile: Dict[str, Any]) -> List[str]:
    issue_policy = profile.get("issue_policy", {})
    findings: List[str] = []
    if issue_policy.get("mode") not in {"zero", "ratchet", "audit"}:
        findings.append(f"{profile['slug']}: issue_policy.mode must be zero, ratchet, or audit")
    if issue_policy.get("pr_behavior") not in {"introduced_only", "absolute"}:
        findings.append(f"{profile['slug']}: issue_policy.pr_behavior must be introduced_only or absolute")
    if issue_policy.get("main_behavior") != "absolute":
        findings.append(f"{profile['slug']}: issue_policy.main_behavior must be absolute")
    return findings


def _validate_deps_contract(profile: Dict[str, Any]) -> List[str]:
    deps = profile.get("deps", {})
    findings: List[str] = []
    if deps.get("policy") not in {"zero_critical", "zero_high", "zero_any"}:
        findings.append(f"{profile['slug']}: deps.policy must be zero_critical, zero_high, or zero_any")
    if deps.get("scope") not in {"runtime", "all"}:
        findings.append(f"{profile['slug']}: deps.scope must be runtime or all")
    return findings


def _validate_visual_pair_contract(profile: Dict[str, Any]) -> List[str]:
    if not profile.get("visual_pair_required"):
        return []

    findings: List[str] = []
    vendors = profile["vendors"]
    chromatic = vendors["chromatic"]["status_context"]
    applitools = vendors["applitools"]["status_context"]
    ruleset_contexts = set(active_required_contexts(profile, event_name="ruleset"))
    target_contexts = set(profile["required_contexts"].get("target", []))
    paired_contexts = [
        (ruleset_contexts, "required_now"),
        (target_contexts, "target"),
    ]
    findings.extend(
        f"{profile['slug']}: visual_pair_required needs both Chromatic and Applitools contexts in {label}"
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
    coverage = profile.get("coverage", {})
    if not profile.get("enabled_scanners", {}).get("coverage", False):
        return []

    findings = _require_values(profile["slug"], [("coverage.command", coverage.get("command"))])
    if not coverage.get("inputs"):
        findings.append(f"{profile['slug']}: coverage.inputs must declare at least one report")
    if coverage.get("shell") not in {"bash", "pwsh"}:
        findings.append(f"{profile['slug']}: coverage.shell must be bash or pwsh")
    findings.extend(
        f"{profile['slug']}: coverage.assert_mode.{mode_name} must be enforce, evidence_only, or non_regression"
        for mode_name, mode_value in coverage.get("assert_mode", {}).items()
        if mode_value not in {"enforce", "evidence_only", "non_regression"}
    )
    if coverage.get("require_sources_mode") not in {"explicit", "infer", "disabled"}:
        findings.append(f"{profile['slug']}: coverage.require_sources_mode must be explicit, infer, or disabled")
    return findings


def _validate_vendor_urls(profile: Dict[str, Any]) -> List[str]:
    findings: List[str] = []
    for vendor_name, vendor_payload in profile["vendors"].items():
        if not isinstance(vendor_payload, dict):
            continue
        for key, value in vendor_payload.items():
            if key.endswith("_url") and value:
                try:
                    normalize_https_url(str(value))
                except ValueError as exc:
                    findings.append(f"{profile['slug']}: invalid {vendor_name}.{key}: {exc}")
    return findings


def validate_profile(profile: Dict[str, Any]) -> List[str]:
    findings: List[str] = validate_profile_shape(profile, slug=profile["slug"])
    for validator in (
        _validate_control_plane_lanes,
        _validate_required_context_sets,
        _validate_secret_contract,
        _validate_issue_policy_contract,
        _validate_deps_contract,
        _validate_visual_pair_contract,
        _validate_coverage_contract,
        _validate_vendor_urls,
    ):
        findings.extend(validator(profile))
    return findings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve control-plane repo profiles and rulesets.")
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY_PATH))
    parser.add_argument("--repo-slug", required=True)
    parser.add_argument("--event-name", default="pull_request")
    parser.add_argument("--print", choices=("profile", "ruleset", "contexts"), default="profile")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    inventory = load_inventory(args.inventory)
    profile = load_repo_profile(inventory, args.repo_slug)
    if args.print == "profile":
        print(json.dumps(profile, indent=2, sort_keys=True))
    elif args.print == "ruleset":
        print(json.dumps(build_ruleset_payload(profile), indent=2, sort_keys=True))
    else:
        print(json.dumps(active_required_contexts(profile, event_name=args.event_name), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
