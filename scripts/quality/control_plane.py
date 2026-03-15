from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml  # type: ignore[import-untyped]

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import dedupe_strings
from scripts.security_helpers import normalize_https_url


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY_PATH = ROOT / "inventory" / "repos.yml"


def repo_root() -> Path:
    return ROOT


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected mapping at {path}")
    return payload


def _deep_merge(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged: dict[str, Any] = deepcopy(base)
        for key, value in overlay.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged
    return deepcopy(overlay)


def _inventory_root(inventory: dict[str, Any]) -> Path:
    return Path(inventory["_root"])


def load_inventory(path: Path | str = DEFAULT_INVENTORY_PATH) -> dict[str, Any]:
    inventory_path = Path(path).resolve()
    payload = _load_yaml(inventory_path)
    payload.setdefault("version", 1)
    payload.setdefault("repos", [])
    if not isinstance(payload["repos"], list):
        raise ValueError("inventory repos must be a list")
    payload["_inventory_path"] = str(inventory_path)
    payload["_root"] = str(inventory_path.parent.parent)
    return payload


def _load_stack(inventory: dict[str, Any], stack_id: str, seen: set[str] | None = None) -> dict[str, Any]:
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

    merged: dict[str, Any] = {}
    for parent in parents:
        merged = _deep_merge(merged, _load_stack(inventory, str(parent), seen=trail))
    merged = _deep_merge(merged, payload)
    merged.setdefault("id", stack_id)
    return merged


def _normalize_required_contexts(raw: dict[str, Any]) -> dict[str, list[str]]:
    always = dedupe_strings(raw.get("always", []))
    pull_request_only = [item for item in dedupe_strings(raw.get("pull_request_only", [])) if item not in always]
    required_now = dedupe_strings(raw.get("required_now", []) or [*always, *pull_request_only])
    target = dedupe_strings(raw.get("target", []) or [*required_now])
    return {
        "always": always,
        "pull_request_only": pull_request_only,
        "required_now": required_now,
        "target": target,
    }


def _merge_required_contexts(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, list[str]]:
    return _normalize_required_contexts(
        {
            "always": [*base.get("always", []), *overlay.get("always", [])],
            "pull_request_only": [*base.get("pull_request_only", []), *overlay.get("pull_request_only", [])],
            "required_now": [*base.get("required_now", []), *overlay.get("required_now", [])],
            "target": [*base.get("target", []), *overlay.get("target", [])],
        }
    )


def _normalize_coverage_inputs(raw_inputs: Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(raw_inputs, list):
        return normalized

    for item in raw_inputs:
        if not isinstance(item, dict):
            continue
        fmt = str(item.get("format", "")).strip().lower()
        name = str(item.get("name", "")).strip()
        path = str(item.get("path", "")).strip()
        if fmt not in {"xml", "lcov"} or not name or not path:
            continue
        normalized.append({"format": fmt, "name": name, "path": path})
    return normalized


def _infer_coverage_inputs(coverage: dict[str, Any]) -> list[dict[str, str]]:
    inputs = _normalize_coverage_inputs(coverage.get("inputs", []))
    legacy_path = str(coverage.get("artifact_path", "")).strip()
    if inputs or not legacy_path:
        return inputs

    inferred = "xml" if legacy_path.endswith(".xml") else "lcov"
    return [{"format": inferred, "name": "default", "path": legacy_path}]


def _normalize_java_setup(raw_java: Any) -> dict[str, Any]:
    if isinstance(raw_java, str):
        raw_java = {"distribution": "temurin", "version": raw_java}
    java = deepcopy(raw_java) if isinstance(raw_java, dict) else {}
    return {
        "distribution": str(java.get("distribution", "")).strip(),
        "version": str(java.get("version", "")).strip(),
    }


def _normalize_coverage_setup(raw_setup: Any) -> dict[str, Any]:
    setup = deepcopy(raw_setup) if isinstance(raw_setup, dict) else {}
    return {
        "python": str(setup.get("python", "")).strip(),
        "node": str(setup.get("node", "")).strip(),
        "go": str(setup.get("go", "")).strip(),
        "dotnet": str(setup.get("dotnet", "")).strip(),
        "rust": bool(setup.get("rust", False)),
        "system_packages": dedupe_strings(setup.get("system_packages", [])),
        "java": _normalize_java_setup(setup.get("java", {})),
    }


def _normalize_coverage_assert_mode(raw_assert_mode: Any) -> dict[str, str]:
    if isinstance(raw_assert_mode, str):
        raw_assert_mode = {"default": raw_assert_mode}

    assert_mode = {"default": "enforce"}
    if isinstance(raw_assert_mode, dict):
        for key, value in raw_assert_mode.items():
            text = str(value or "").strip()
            if text:
                assert_mode[str(key)] = text
    return assert_mode


def _normalize_coverage(raw: dict[str, Any]) -> dict[str, Any]:
    coverage = deepcopy(raw or {})
    inputs = _infer_coverage_inputs(coverage)
    coverage["runner"] = str(coverage.get("runner", "ubuntu-latest")).strip() or "ubuntu-latest"
    coverage["shell"] = str(coverage.get("shell", "bash")).strip() or "bash"
    coverage["command"] = str(coverage.get("command", "")).strip()
    coverage["inputs"] = inputs
    coverage["require_sources"] = dedupe_strings(coverage.get("require_sources", []))
    coverage["min_percent"] = float(coverage.get("min_percent", 100.0))
    coverage["assert_mode"] = _normalize_coverage_assert_mode(coverage.get("assert_mode", {}))
    coverage["evidence_note"] = str(coverage.get("evidence_note", "")).strip()
    coverage["setup"] = _normalize_coverage_setup(coverage.get("setup", {}))
    return coverage


def _normalize_codex_environment(raw: dict[str, Any], *, verify_command: str) -> dict[str, Any]:
    payload = deepcopy(raw or {})
    return {
        "mode": str(payload.get("mode", "automatic")).strip() or "automatic",
        "verify_command": str(payload.get("verify_command", verify_command)).strip() or verify_command,
        "auth_file": str(payload.get("auth_file", "~/.codex/auth.json")).strip() or "~/.codex/auth.json",
        "network_profile": str(payload.get("network_profile", "unrestricted")).strip() or "unrestricted",
        "methods": str(payload.get("methods", "all")).strip() or "all",
        "runner_labels": dedupe_strings(payload.get("runner_labels", ["self-hosted", "codex-trusted"])),
    }


def _normalize_visual_lane(raw: dict[str, Any]) -> dict[str, str]:
    payload = deepcopy(raw or {})
    return {
        "kind": str(payload.get("kind", "none")).strip() or "none",
    }


def _ensure_vendor_url(
    vendor: dict[str, Any],
    key: str,
    url: str,
    *,
    allowed_host_suffixes: set[str],
) -> None:
    if not vendor.get(key):
        vendor[key] = normalize_https_url(url, allowed_host_suffixes=allowed_host_suffixes)


def _finalize_sonar_vendor(vendors: dict[str, Any]) -> None:
    sonar = vendors.setdefault("sonar", {})
    project_key = str(sonar.get("project_key", "")).strip()
    if project_key:
        _ensure_vendor_url(
            sonar,
            "dashboard_url",
            f"https://sonarcloud.io/project/overview?id={quote(project_key, safe='')}",
            allowed_host_suffixes={"sonarcloud.io"},
        )


def _finalize_codacy_vendor(vendors: dict[str, Any], *, owner: str, repo_name: str) -> None:
    codacy = vendors.setdefault("codacy", {})
    codacy.setdefault("provider", "gh")
    codacy.setdefault("owner", owner)
    codacy.setdefault("repo", repo_name)
    codacy.setdefault("profile_mode", "defaults_all_languages")
    _ensure_vendor_url(
        codacy,
        "dashboard_url",
        f"https://app.codacy.com/gh/{quote(owner, safe='')}/{quote(repo_name, safe='')}/dashboard",
        allowed_host_suffixes={"codacy.com"},
    )


def _finalize_codecov_vendor(vendors: dict[str, Any], *, owner: str, repo_name: str) -> None:
    _ensure_vendor_url(
        vendors.setdefault("codecov", {}),
        "dashboard_url",
        f"https://app.codecov.io/gh/{quote(owner, safe='')}/{quote(repo_name, safe='')}",
        allowed_host_suffixes={"codecov.io"},
    )


def _finalize_qlty_vendor(vendors: dict[str, Any], *, owner: str, repo_name: str) -> None:
    qlty = vendors.setdefault("qlty", {})
    qlty.setdefault("project_slug", f"{owner}/{repo_name}")
    qlty.setdefault("gate_context", "qlty check")
    qlty.setdefault("coverage_context", "qlty coverage")
    qlty.setdefault("diff_coverage_context", "qlty coverage diff")
    qlty.setdefault(
        "check_names_actual",
        [
            qlty["gate_context"],
            qlty["coverage_context"],
            qlty["diff_coverage_context"],
        ],
    )
    qlty.setdefault("diff_coverage_percent", 100)
    qlty.setdefault("total_coverage_policy", "fail_on_any_drop")
    _ensure_vendor_url(
        qlty,
        "dashboard_url",
        f"https://qlty.sh/gh/{quote(owner, safe='')}/projects/{quote(repo_name, safe='')}",
        allowed_host_suffixes={"qlty.sh"},
    )


def _provider_env_suffix(repo_name: str) -> str:
    suffix = []
    previous_was_separator = False
    for char in repo_name:
        if char.isalnum():
            suffix.append(char.upper())
            previous_was_separator = False
            continue
        if not previous_was_separator:
            suffix.append("_")
            previous_was_separator = True
    normalized = "".join(suffix).strip("_")
    return normalized or "REPO"


def _finalize_visual_vendors(profile: dict[str, Any], vendors: dict[str, Any]) -> None:
    if not profile.get("visual_pair_required"):
        return

    repo_name = profile["repo_name"]
    chromatic = vendors.setdefault("chromatic", {})
    chromatic.setdefault("project_name", repo_name)
    chromatic.setdefault("token_secret", "CHROMATIC_PROJECT_TOKEN")
    chromatic.setdefault("local_env_var", f"CHROMATIC_PROJECT_TOKEN_{_provider_env_suffix(repo_name)}")

    applitools = vendors.setdefault("applitools", {})
    applitools.setdefault("project_name", repo_name)


def _finalize_passthrough_vendors(vendors: dict[str, Any]) -> None:
    vendors.setdefault("deepscan", {}).setdefault("open_issues_url_var", "DEEPSCAN_OPEN_ISSUES_URL")
    sentry = vendors.setdefault("sentry", {})
    sentry.setdefault("org_var", "SENTRY_ORG")
    sentry.setdefault("project_vars", ["SENTRY_PROJECT"])
    vendors.setdefault("chromatic", {}).setdefault("status_context", "Chromatic Playwright")
    vendors.setdefault("applitools", {}).setdefault("status_context", "Applitools Visual")


def _finalize_vendors(profile: dict[str, Any]) -> dict[str, Any]:
    owner = profile["owner"]
    repo_name = profile["repo_name"]
    vendor_source = _deep_merge(profile.get("vendors", {}), profile.get("providers", {}))
    vendors = deepcopy(vendor_source)

    _finalize_sonar_vendor(vendors)
    _finalize_codacy_vendor(vendors, owner=owner, repo_name=repo_name)
    _finalize_codecov_vendor(vendors, owner=owner, repo_name=repo_name)
    _finalize_qlty_vendor(vendors, owner=owner, repo_name=repo_name)
    _finalize_passthrough_vendors(vendors)
    _finalize_visual_vendors(profile, vendors)

    github = vendors.setdefault("github", {})
    github["repo_url"] = normalize_https_url(
        f"https://github.com/{quote(owner, safe='')}/{quote(repo_name, safe='')}",
        allowed_host_suffixes={"github.com"},
    )

    return vendors


def _resolve_repo_sources(inventory: dict[str, Any], repo_slug: str) -> tuple[dict[str, Any], str, dict[str, Any], str, dict[str, Any]]:
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
    return repo_entry, profile_id, repo_profile, stack_id, stack_profile


def _merge_repo_profile(stack_profile: dict[str, Any], repo_profile: dict[str, Any]) -> tuple[dict[str, Any], str]:
    merged = _deep_merge(stack_profile, repo_profile)
    required_contexts_mode = str(repo_profile.get("required_contexts_mode", "merge")).strip() or "merge"
    if required_contexts_mode == "replace":
        merged["required_contexts"] = _normalize_required_contexts(repo_profile.get("required_contexts", {}))
    else:
        merged["required_contexts"] = _merge_required_contexts(
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


def _apply_inventory_overrides(
    merged: dict[str, Any],
    *,
    repo_entry: dict[str, Any],
    repo_slug: str,
    profile_id: str,
    stack_id: str,
    required_contexts_mode: str,
) -> dict[str, Any]:
    return _deep_merge(
        merged,
        {
            "slug": repo_slug,
            "default_branch": repo_entry.get("default_branch", merged.get("default_branch", "main")),
            "rollout": repo_entry.get("rollout", merged.get("rollout", "")),
            "rollout_notes": repo_entry.get("notes", ""),
            "profile_id": profile_id,
            "stack": stack_id,
            "required_contexts_mode": required_contexts_mode,
        },
    )


def _finalize_repo_profile(merged: dict[str, Any], repo_slug: str) -> dict[str, Any]:
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
    merged["required_contexts"] = _normalize_required_contexts(merged.get("required_contexts", {}))
    merged["enabled_scanners"] = merged.get("enabled_scanners", {})
    merged["coverage"] = _normalize_coverage(merged.get("coverage", {}))
    merged["codex_environment"] = _normalize_codex_environment(
        merged.get("codex_environment", {}),
        verify_command=merged["verify_command"],
    )
    merged["visual_pair_required"] = bool(merged.get("visual_pair_required", False))
    merged["visual_lane"] = _normalize_visual_lane(merged.get("visual_lane", {}))
    merged["ruleset_mode"] = str(merged.get("ruleset_mode", "strict-zero-phase1")).strip()
    merged["preserve_public_check_names"] = bool(merged.get("preserve_public_check_names", True))
    merged["vendors"] = _finalize_vendors(merged)
    merged["providers"] = deepcopy(merged["vendors"])
    return merged


def load_repo_profile(inventory: dict[str, Any], repo_slug: str) -> dict[str, Any]:
    repo_entry, profile_id, repo_profile, stack_id, stack_profile = _resolve_repo_sources(inventory, repo_slug)
    merged, required_contexts_mode = _merge_repo_profile(stack_profile, repo_profile)
    merged = _apply_inventory_overrides(
        merged,
        repo_entry=repo_entry,
        repo_slug=repo_slug,
        profile_id=profile_id,
        stack_id=stack_id,
        required_contexts_mode=required_contexts_mode,
    )
    return _finalize_repo_profile(merged, repo_slug)


def active_required_contexts(profile: dict[str, Any], *, event_name: str) -> list[str]:
    required_contexts = profile["required_contexts"]
    if event_name == "ruleset":
        return dedupe_strings(required_contexts.get("required_now", []))

    required = list(required_contexts["always"])
    if event_name in {"pull_request", "pull_request_target"}:
        required.extend(required_contexts["pull_request_only"])
    return dedupe_strings(required)


def build_ruleset_payload(profile: dict[str, Any]) -> dict[str, Any]:
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


def _validate_codex_environment(profile: dict[str, Any], codex_environment: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    if codex_environment.get("mode") != "automatic":
        findings.append(f"{profile['slug']}: codex_environment.mode must be automatic")
    if not codex_environment.get("verify_command"):
        findings.append(f"{profile['slug']}: codex_environment.verify_command is required")
    if not codex_environment.get("auth_file"):
        findings.append(f"{profile['slug']}: codex_environment.auth_file is required")
    if codex_environment.get("network_profile") != "unrestricted":
        findings.append(f"{profile['slug']}: codex_environment.network_profile must be unrestricted")
    if codex_environment.get("methods") != "all":
        findings.append(f"{profile['slug']}: codex_environment.methods must be all")
    runner_labels = codex_environment.get("runner_labels", [])
    if not runner_labels:
        findings.append(f"{profile['slug']}: codex_environment.runner_labels is required")
    elif "self-hosted" not in runner_labels:
        findings.append(f"{profile['slug']}: codex_environment.runner_labels must include self-hosted")
    return findings


def _validate_control_plane_lanes(profile: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    if not profile.get("verify_command"):
        findings.append(f"{profile['slug']}: verify_command is required")
    if profile.get("github_mutation_lane") != "codex-private-runner":
        findings.append(f"{profile['slug']}: github_mutation_lane must be codex-private-runner")
    if profile.get("codex_auth_lane") != "chatgpt-account":
        findings.append(f"{profile['slug']}: codex_auth_lane must be chatgpt-account")
    if profile.get("provider_ui_mode") != "playwright-manual-login":
        findings.append(f"{profile['slug']}: provider_ui_mode must be playwright-manual-login")

    findings.extend(_validate_codex_environment(profile, profile.get("codex_environment", {})))
    return findings


def _validate_required_context_sets(profile: dict[str, Any]) -> list[str]:
    findings: list[str] = []
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


def _validate_secret_contract(profile: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    duplicate_conditional = [name for name in profile.get("conditional_secrets", []) if name in profile.get("required_secrets", [])]
    if duplicate_conditional:
        findings.append(
            f"{profile['slug']}: conditional_secrets duplicates required_secrets for {', '.join(duplicate_conditional)}"
        )
    if "OPENAI_API_KEY" in profile.get("required_secrets", []):
        findings.append(f"{profile['slug']}: OPENAI_API_KEY must not be part of required_secrets")
    return findings


def _validate_visual_pair_contract(profile: dict[str, Any]) -> list[str]:
    if not profile.get("visual_pair_required"):
        return []

    findings: list[str] = []
    chromatic = profile["vendors"]["chromatic"]["status_context"]
    applitools = profile["vendors"]["applitools"]["status_context"]
    ruleset_contexts = set(active_required_contexts(profile, event_name="ruleset"))
    target_contexts = set(profile["required_contexts"].get("target", []))
    if (chromatic in ruleset_contexts) != (applitools in ruleset_contexts):
        findings.append(f"{profile['slug']}: visual_pair_required needs both Chromatic and Applitools contexts in required_now")
    if (chromatic in target_contexts) != (applitools in target_contexts):
        findings.append(f"{profile['slug']}: visual_pair_required needs both Chromatic and Applitools contexts in target")
    if not profile["vendors"]["chromatic"].get("project_name"):
        findings.append(f"{profile['slug']}: visual_pair_required requires chromatic.project_name")
    if not profile["vendors"]["chromatic"].get("token_secret"):
        findings.append(f"{profile['slug']}: visual_pair_required requires chromatic.token_secret")
    if not profile["vendors"]["chromatic"].get("local_env_var"):
        findings.append(f"{profile['slug']}: visual_pair_required requires chromatic.local_env_var")
    if not profile["vendors"]["applitools"].get("project_name"):
        findings.append(f"{profile['slug']}: visual_pair_required requires applitools.project_name")
    return findings


def _validate_coverage_contract(profile: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    coverage = profile.get("coverage", {})
    if not profile.get("enabled_scanners", {}).get("coverage", False):
        return findings

    if not coverage.get("command"):
        findings.append(f"{profile['slug']}: coverage.command is required")
    if not coverage.get("inputs"):
        findings.append(f"{profile['slug']}: coverage.inputs must declare at least one report")
    if coverage.get("shell") not in {"bash", "pwsh"}:
        findings.append(f"{profile['slug']}: coverage.shell must be bash or pwsh")
    for mode_name, mode_value in coverage.get("assert_mode", {}).items():
        if mode_value not in {"enforce", "evidence_only"}:
            findings.append(f"{profile['slug']}: coverage.assert_mode.{mode_name} must be enforce or evidence_only")
    return findings


def _validate_vendor_urls(profile: dict[str, Any]) -> list[str]:
    findings: list[str] = []
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


def validate_profile(profile: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    for validator in (
        _validate_control_plane_lanes,
        _validate_required_context_sets,
        _validate_secret_contract,
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
