"""Resolve governed repo profiles, required contexts, and rulesets."""

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

from scripts.quality import profile_contract_validation
from scripts.quality.common import (
    _deep_merge as common_deep_merge,
)
from scripts.quality.common import (
    dedupe_strings,
)
from scripts.quality.control_plane_vendors import (
    finalize_vendors,
    normalize_visual_lane,
)
from scripts.quality.profile_normalization import (
    infer_coverage_inputs as common_infer_coverage_inputs,
)
from scripts.quality.profile_normalization import (
    merge_required_contexts as common_merge_required_contexts,
)
from scripts.quality.profile_normalization import (
    normalize_codeql as common_normalize_codeql,
)
from scripts.quality.profile_normalization import (
    normalize_codex_environment as common_normalize_codex_environment,
)
from scripts.quality.profile_normalization import (
    normalize_coverage as common_normalize_coverage,
)
from scripts.quality.profile_normalization import (
    normalize_coverage_assert_mode as common_normalize_coverage_assert_mode,
)
from scripts.quality.profile_normalization import (
    normalize_coverage_inputs as common_normalize_coverage_inputs,
)
from scripts.quality.profile_normalization import (
    normalize_dependabot as common_normalize_dependabot,
)
from scripts.quality.profile_normalization import (
    normalize_deps as common_normalize_deps,
)
from scripts.quality.profile_normalization import (
    normalize_issue_policy as common_normalize_issue_policy,
)
from scripts.quality.profile_normalization import (
    normalize_java_setup as common_normalize_java_setup,
)
from scripts.quality.profile_normalization import (
    normalize_mode as common_normalize_mode,
)
from scripts.quality.profile_normalization import (
    normalize_overrides as common_normalize_overrides,
)
from scripts.quality.profile_normalization import (
    normalize_profile_version as common_normalize_profile_version,
)
from scripts.quality.profile_normalization import (
    normalize_required_contexts as common_normalize_required_contexts,
)
from scripts.quality.profile_normalization import (
    normalize_scanners as common_normalize_scanners,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY_PATH = ROOT / "inventory" / "repos.yml"


@dataclass(frozen=True)
class RepoSources:
    """Bundle the resolved inventory, stack, and repo profile inputs."""

    repo_entry: Dict[str, Any]
    profile_id: str
    repo_profile: Dict[str, Any]
    stack_id: str
    stack_profile: Dict[str, Any]


@dataclass(frozen=True)
class InventoryOverrides:
    """Capture inventory-sourced overrides layered onto a repo profile."""

    repo_entry: Dict[str, Any]
    repo_slug: str
    profile_id: str
    stack_id: str
    required_contexts_mode: str


def repo_root() -> Path:
    """Return the repository root for the control-plane workspace."""
    return ROOT


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Load one YAML mapping from disk."""
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected mapping at {path}")
    return payload


def _deep_merge(base: Any, overlay: Any) -> Any:
    """Delegate to the shared deep-merge helper."""
    return common_deep_merge(base, overlay)


def _inventory_root(inventory: Dict[str, Any]) -> Path:
    """Return the recorded inventory root path."""
    return Path(inventory["_root"])


def load_inventory(path: Path | str = DEFAULT_INVENTORY_PATH) -> Dict[str, Any]:
    """Load the repo inventory and attach derived root metadata."""
    inventory_path = Path(path).resolve()
    payload = _load_yaml(inventory_path)
    payload.setdefault("version", 1)
    payload.setdefault("repos", [])
    if not isinstance(payload["repos"], list):
        raise ValueError("inventory repos must be a list")
    payload["_inventory_path"] = str(inventory_path)
    payload["_root"] = str(inventory_path.parent.parent)
    return payload


def _load_stack(
    inventory: Dict[str, Any], stack_id: str, seen: Set[str] | None = None
) -> Dict[str, Any]:
    """Load one stack profile, expanding parent inheritance recursively."""
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
    """Normalize required-context blocks through the shared helper."""
    return common_normalize_required_contexts(raw)


def _merge_required_contexts(
    base: Dict[str, Any], overlay: Dict[str, Any]
) -> Dict[str, List[str]]:
    """Merge stack and repo required-context blocks."""
    return common_merge_required_contexts(base, overlay)


def _normalize_coverage_inputs(raw_inputs: Any) -> List[Dict[str, str]]:
    """Normalize coverage input mappings through the shared helper."""
    return common_normalize_coverage_inputs(raw_inputs)


def _infer_coverage_inputs(coverage: Dict[str, Any]) -> List[Dict[str, str]]:
    """Infer coverage inputs from coverage metadata."""
    return common_infer_coverage_inputs(coverage)


def _normalize_java_setup(raw_java: Any) -> Dict[str, Any]:
    """Normalize Java setup metadata for coverage runs."""
    return common_normalize_java_setup(raw_java)


def _normalize_coverage_setup(raw_setup: Any) -> Dict[str, Any]:
    """Normalize the runtime setup block used by coverage gates."""
    coverage = common_normalize_coverage({"setup": raw_setup})
    return cast(Dict[str, Any], coverage["setup"])


def _normalize_coverage_assert_mode(raw_assert_mode: Any) -> Dict[str, str]:
    """Normalize the coverage assert-mode configuration."""
    return common_normalize_coverage_assert_mode(raw_assert_mode)


def _normalize_coverage(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize the full coverage configuration block."""
    return common_normalize_coverage(raw)


def _normalize_codex_environment(
    raw: Dict[str, Any], *, verify_command: str
) -> Dict[str, Any]:
    """Normalize the Codex environment contract."""
    return common_normalize_codex_environment(raw, verify_command=verify_command)


def _normalize_issue_policy(raw: Dict[str, Any]) -> Dict[str, str]:
    """Normalize the issue-policy contract."""
    return common_normalize_issue_policy(raw)


def _resolve_repo_sources(inventory: Dict[str, Any], repo_slug: str) -> RepoSources:
    """Resolve inventory, repo, and stack sources for one repository slug."""
    repo_entry = next(
        (item for item in inventory["repos"] if item.get("slug") == repo_slug),
        None,
    )
    if repo_entry is None:
        raise KeyError(f"Repo {repo_slug} not found in inventory")

    profile_id = str(repo_entry.get("profile", "")).strip()
    if not profile_id:
        raise ValueError(f"Repo {repo_slug} is missing a profile id")

    profile_path = (
        _inventory_root(inventory) / "profiles" / "repos" / f"{profile_id}.yml"
    )
    repo_profile = _load_yaml(profile_path)
    stack_id = str(repo_profile.get("stack", "")).strip()
    stack_profile = _load_stack(inventory, stack_id)
    return RepoSources(repo_entry, profile_id, repo_profile, stack_id, stack_profile)


def _merge_repo_profile(
    stack_profile: Dict[str, Any], repo_profile: Dict[str, Any]
) -> Tuple[Dict[str, Any], str]:
    """Merge a stack profile with repo-specific overrides."""
    merged = _deep_merge(stack_profile, repo_profile)
    required_contexts_mode = (
        str(repo_profile.get("required_contexts_mode", "merge")).strip() or "merge"
    )
    if required_contexts_mode == "replace":
        merged["required_contexts"] = common_normalize_required_contexts(
            repo_profile.get("required_contexts", {})
        )
    else:
        merged["required_contexts"] = common_merge_required_contexts(
            stack_profile.get("required_contexts", {}),
            repo_profile.get("required_contexts", {}),
        )
    merged["required_secrets"] = dedupe_strings(
        [
            *stack_profile.get("required_secrets", []),
            *repo_profile.get("required_secrets", []),
        ]
    )
    merged["conditional_secrets"] = dedupe_strings(
        [
            *stack_profile.get("conditional_secrets", []),
            *repo_profile.get("conditional_secrets", []),
        ]
    )
    merged["required_vars"] = dedupe_strings(
        [
            *stack_profile.get("required_vars", []),
            *repo_profile.get("required_vars", []),
        ]
    )
    merged["providers"] = _deep_merge(
        stack_profile.get("providers", {}),
        repo_profile.get("providers", {}),
    )
    merged["vendors"] = _deep_merge(
        stack_profile.get("vendors", {}),
        repo_profile.get("vendors", {}),
    )
    return merged, required_contexts_mode


def _apply_inventory_overrides(
    merged: Dict[str, Any], overrides: InventoryOverrides | Dict[str, Any]
) -> Dict[str, Any]:
    """Apply inventory metadata to one merged repo profile."""
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
            "default_branch": overrides.repo_entry.get(
                "default_branch", merged.get("default_branch", "main")
            ),
            "rollout": overrides.repo_entry.get("rollout", merged.get("rollout", "")),
            "rollout_notes": overrides.repo_entry.get("notes", ""),
            "profile_id": overrides.profile_id,
            "stack": overrides.stack_id,
            "required_contexts_mode": overrides.required_contexts_mode,
        },
    )


def _finalize_repo_profile(merged: Dict[str, Any], repo_slug: str) -> Dict[str, Any]:
    """Finalize a merged profile into the shape consumed by the gates."""
    owner, repo_name = repo_slug.split("/", 1)
    merged["owner"] = owner
    merged["repo_name"] = repo_name
    merged["verify_command"] = str(
        merged.get("verify_command", "bash scripts/verify")
    ).strip()
    merged["github_mutation_lane"] = (
        str(merged.get("github_mutation_lane", "codex-private-runner")).strip()
        or "codex-private-runner"
    )
    merged["codex_auth_lane"] = (
        str(merged.get("codex_auth_lane", "chatgpt-account")).strip()
        or "chatgpt-account"
    )
    merged["provider_ui_mode"] = (
        str(merged.get("provider_ui_mode", "playwright-manual-login")).strip()
        or "playwright-manual-login"
    )
    merged["required_secrets"] = dedupe_strings(merged.get("required_secrets", []))
    merged["conditional_secrets"] = dedupe_strings(
        merged.get("conditional_secrets", [])
    )
    merged["required_vars"] = dedupe_strings(merged.get("required_vars", []))
    _finalize_normalized_profile_sections(merged)
    merged["visual_pair_required"] = bool(merged.get("visual_pair_required", False))
    merged["visual_lane"] = normalize_visual_lane(merged.get("visual_lane", {}))
    merged["ruleset_mode"] = str(
        merged.get("ruleset_mode", "strict-zero-phase1")
    ).strip()
    merged["preserve_public_check_names"] = bool(
        merged.get("preserve_public_check_names", True)
    )
    vendor_source = _deep_merge(merged.get("vendors", {}), merged.get("providers", {}))
    merged["vendors"] = finalize_vendors(merged, vendor_source)
    merged["providers"] = deepcopy(merged["vendors"])
    return merged


def _finalize_normalized_profile_sections(merged: Dict[str, Any]) -> None:
    """Normalize the shared profile sections after merge resolution."""
    merged["required_contexts"] = common_normalize_required_contexts(
        merged.get("required_contexts", {})
    )
    merged["enabled_scanners"] = merged.get("enabled_scanners", {})
    merged["issue_policy"] = common_normalize_issue_policy(
        merged.get("issue_policy", {})
    )
    merged["deps"] = common_normalize_deps(merged.get("deps", {}))
    merged["enabled_scanners"]["deps"] = bool(merged["deps"]["enabled"])
    merged["codeql"] = common_normalize_codeql(merged.get("codeql", {}))
    merged["dependabot"] = common_normalize_dependabot(merged.get("dependabot", {}))
    merged["coverage"] = common_normalize_coverage(merged.get("coverage", {}))
    merged["codex_environment"] = common_normalize_codex_environment(
        merged.get("codex_environment", {}),
        verify_command=merged["verify_command"],
    )
    # v2 schema fields (docs/QZP-V2-DESIGN.md §3): synthesise `version`,
    # `mode`, `scanners`, and `overrides` on every profile so downstream
    # consumers never have to re-check "is this v1 or v2?" — the canonical
    # output is identical. v1 legacy values still win where v2 is absent.
    merged["version"] = common_normalize_profile_version(merged.get("version"))
    merged["mode"] = common_normalize_mode(
        merged.get("mode"),
        legacy_issue_policy=merged["issue_policy"],
    )
    merged["scanners"] = common_normalize_scanners(
        merged.get("scanners"),
        legacy_enabled_scanners=merged["enabled_scanners"],
    )
    merged["overrides"] = common_normalize_overrides(merged.get("overrides"))


def load_repo_profile(inventory: Dict[str, Any], repo_slug: str) -> Dict[str, Any]:
    """Load, merge, and finalize one repo profile from inventory."""
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
    """Return required contexts for one workflow event."""
    required_contexts = profile["required_contexts"]
    target = dedupe_strings(required_contexts.get("target", []))
    required_now = dedupe_strings(required_contexts.get("required_now", []))
    always = dedupe_strings(required_contexts.get("always", []))
    pr_only = [
        item
        for item in dedupe_strings(required_contexts.get("pull_request_only", []))
        if item not in always
    ]

    target_contexts = target or required_now or dedupe_strings([*always, *pr_only])
    if event_name in {"ruleset", "pull_request", "pull_request_target", "merge_group"}:
        return target_contexts
    if event_name == "push":
        return always

    return required_now or always


def build_ruleset_payload(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Build the GitHub ruleset payload for one governed repository."""
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
                    "required_approving_review_count": 0,
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
                    "required_status_checks": [
                        {"context": name}
                        for name in contexts
                    ],
                },
            },
            {"type": "non_fast_forward"},
            {"type": "deletion"},
        ],
    }


def validate_profile(profile: Dict[str, Any]) -> List[str]:
    """Validate the merged profile with the shared contract rules."""
    return profile_contract_validation.validate_profile(
        profile,
        active_required_contexts_fn=active_required_contexts,
    )


def _validate_coverage_contract(profile: Dict[str, Any]) -> List[str]:
    """Validate the coverage contract portion of one profile."""
    return profile_contract_validation._validate_coverage_contract(profile)


def _validate_vendor_urls(profile: Dict[str, Any]) -> List[str]:
    """Validate vendor URLs for one profile."""
    return profile_contract_validation._validate_vendor_urls(profile)


def _parse_args() -> argparse.Namespace:
    """Parse control-plane CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Resolve control-plane repo profiles and rulesets."
    )
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY_PATH))
    parser.add_argument("--repo-slug", required=True)
    parser.add_argument("--event-name", default="pull_request")
    parser.add_argument(
        "--print",
        choices=("profile", "ruleset", "contexts"),
        default="profile",
    )
    return parser.parse_args()


def main() -> int:
    """Execute the control-plane CLI."""
    args = _parse_args()
    inventory = load_inventory(args.inventory)
    profile = load_repo_profile(inventory, args.repo_slug)
    if args.print == "profile":
        print(json.dumps(profile, indent=2, sort_keys=True))
    elif args.print == "ruleset":
        print(json.dumps(build_ruleset_payload(profile), indent=2, sort_keys=True))
    else:
        print(
            json.dumps(
                active_required_contexts(profile, event_name=args.event_name),
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
