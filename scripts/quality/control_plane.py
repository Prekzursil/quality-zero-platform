from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

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
    return {"always": always, "pull_request_only": pull_request_only}


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


def _normalize_coverage(raw: dict[str, Any]) -> dict[str, Any]:
    coverage = deepcopy(raw or {})
    inputs = _normalize_coverage_inputs(coverage.get("inputs", []))

    legacy_path = str(coverage.get("artifact_path", "")).strip()
    if not inputs and legacy_path:
        inferred = "xml" if legacy_path.endswith(".xml") else "lcov"
        inputs = [{"format": inferred, "name": "default", "path": legacy_path}]

    raw_setup = coverage.get("setup", {})
    setup = deepcopy(raw_setup) if isinstance(raw_setup, dict) else {}
    raw_java = setup.get("java", {})
    if isinstance(raw_java, str):
        raw_java = {"distribution": "temurin", "version": raw_java}
    java = deepcopy(raw_java) if isinstance(raw_java, dict) else {}

    raw_assert_mode = coverage.get("assert_mode", {})
    if isinstance(raw_assert_mode, str):
        raw_assert_mode = {"default": raw_assert_mode}
    assert_mode = {"default": "enforce"}
    if isinstance(raw_assert_mode, dict):
        for key, value in raw_assert_mode.items():
            text = str(value or "").strip()
            if text:
                assert_mode[str(key)] = text

    coverage["runner"] = str(coverage.get("runner", "ubuntu-latest")).strip() or "ubuntu-latest"
    coverage["shell"] = str(coverage.get("shell", "bash")).strip() or "bash"
    coverage["command"] = str(coverage.get("command", "")).strip()
    coverage["inputs"] = inputs
    coverage["require_sources"] = dedupe_strings(coverage.get("require_sources", []))
    coverage["min_percent"] = float(coverage.get("min_percent", 100.0))
    coverage["assert_mode"] = assert_mode
    coverage["evidence_note"] = str(coverage.get("evidence_note", "")).strip()
    coverage["setup"] = {
        "python": str(setup.get("python", "")).strip(),
        "node": str(setup.get("node", "")).strip(),
        "go": str(setup.get("go", "")).strip(),
        "dotnet": str(setup.get("dotnet", "")).strip(),
        "rust": bool(setup.get("rust", False)),
        "system_packages": dedupe_strings(setup.get("system_packages", [])),
        "java": {
            "distribution": str(java.get("distribution", "")).strip(),
            "version": str(java.get("version", "")).strip(),
        },
    }
    return coverage


def _finalize_vendors(profile: dict[str, Any]) -> dict[str, Any]:
    owner = profile["owner"]
    repo_name = profile["repo_name"]
    vendors = deepcopy(profile.get("vendors", {}))

    sonar = vendors.setdefault("sonar", {})
    project_key = str(sonar.get("project_key", "")).strip()
    if project_key and not sonar.get("dashboard_url"):
        sonar["dashboard_url"] = normalize_https_url(
            f"https://sonarcloud.io/project/overview?id={quote(project_key, safe='')}",
            allowed_host_suffixes={"sonarcloud.io"},
        )

    codacy = vendors.setdefault("codacy", {})
    codacy.setdefault("provider", "gh")
    codacy.setdefault("owner", owner)
    codacy.setdefault("repo", repo_name)
    if not codacy.get("dashboard_url"):
        codacy["dashboard_url"] = normalize_https_url(
            f"https://app.codacy.com/gh/{quote(owner, safe='')}/{quote(repo_name, safe='')}/dashboard",
            allowed_host_suffixes={"codacy.com"},
        )

    codecov = vendors.setdefault("codecov", {})
    if not codecov.get("dashboard_url"):
        codecov["dashboard_url"] = normalize_https_url(
            f"https://app.codecov.io/gh/{quote(owner, safe='')}/{quote(repo_name, safe='')}",
            allowed_host_suffixes={"codecov.io"},
        )

    deepscan = vendors.setdefault("deepscan", {})
    deepscan.setdefault("open_issues_url_var", "DEEPSCAN_OPEN_ISSUES_URL")

    sentry = vendors.setdefault("sentry", {})
    sentry.setdefault("org_var", "SENTRY_ORG")
    sentry.setdefault("project_vars", ["SENTRY_PROJECT"])

    github = vendors.setdefault("github", {})
    github["repo_url"] = normalize_https_url(
        f"https://github.com/{quote(owner, safe='')}/{quote(repo_name, safe='')}",
        allowed_host_suffixes={"github.com"},
    )

    return vendors


def load_repo_profile(inventory: dict[str, Any], repo_slug: str) -> dict[str, Any]:
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

    merged = _deep_merge(stack_profile, repo_profile)
    merged = _deep_merge(
        merged,
        {
            "slug": repo_slug,
            "default_branch": repo_entry.get("default_branch", merged.get("default_branch", "main")),
            "rollout": repo_entry.get("rollout", merged.get("rollout", "")),
            "rollout_notes": repo_entry.get("notes", ""),
            "profile_id": profile_id,
            "stack": stack_id,
        },
    )

    owner, repo_name = repo_slug.split("/", 1)
    merged["owner"] = owner
    merged["repo_name"] = repo_name
    merged["verify_command"] = str(merged.get("verify_command", "bash scripts/verify")).strip()
    merged["codex_setup_command"] = str(merged.get("codex_setup_command", "")).strip()
    merged["required_secrets"] = dedupe_strings(merged.get("required_secrets", []))
    merged["required_vars"] = dedupe_strings(merged.get("required_vars", []))
    merged["required_contexts"] = _normalize_required_contexts(merged.get("required_contexts", {}))
    merged["enabled_scanners"] = merged.get("enabled_scanners", {})
    merged["coverage"] = _normalize_coverage(merged.get("coverage", {}))
    merged["ruleset_mode"] = str(merged.get("ruleset_mode", "strict-zero-phase1")).strip()
    merged["preserve_public_check_names"] = bool(merged.get("preserve_public_check_names", True))
    merged["vendors"] = _finalize_vendors(merged)
    return merged


def active_required_contexts(profile: dict[str, Any], *, event_name: str) -> list[str]:
    required = list(profile["required_contexts"]["always"])
    if event_name in {"pull_request", "pull_request_target", "ruleset"}:
        required.extend(profile["required_contexts"]["pull_request_only"])
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


def validate_profile(profile: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    if not profile.get("verify_command"):
        findings.append(f"{profile['slug']}: verify_command is required")
    if not profile.get("codex_setup_command"):
        findings.append(f"{profile['slug']}: codex_setup_command is required")
    if not active_required_contexts(profile, event_name="ruleset"):
        findings.append(f"{profile['slug']}: at least one required context is required")
    coverage = profile.get("coverage", {})
    if profile.get("enabled_scanners", {}).get("coverage", False):
        if not coverage.get("command"):
            findings.append(f"{profile['slug']}: coverage.command is required")
        if not coverage.get("inputs"):
            findings.append(f"{profile['slug']}: coverage.inputs must declare at least one report")
        if coverage.get("shell") not in {"bash", "pwsh"}:
            findings.append(f"{profile['slug']}: coverage.shell must be bash or pwsh")
        for mode_name, mode_value in coverage.get("assert_mode", {}).items():
            if mode_value not in {"enforce", "evidence_only"}:
                findings.append(f"{profile['slug']}: coverage.assert_mode.{mode_name} must be enforce or evidence_only")

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
