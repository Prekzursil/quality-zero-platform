from __future__ import absolute_import

from copy import deepcopy
from typing import Any, Dict, List, Mapping

from scripts.quality.common import dedupe_strings


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


def normalize_coverage_inputs(raw_inputs: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_inputs, list):
        return []

    normalized_items: List[Dict[str, str]] = []
    for item in raw_inputs:
        if not isinstance(item, dict):
            continue
        normalized_item = {
            "format": str(item.get("format", "")).strip().lower(),
            "name": str(item.get("name", "")).strip(),
            "path": str(item.get("path", "")).strip(),
        }
        if normalized_item["format"] in {"xml", "lcov"} and normalized_item["name"] and normalized_item["path"]:
            normalized_items.append(normalized_item)
    return normalized_items


def infer_coverage_inputs(coverage: Mapping[str, Any] | None) -> List[Dict[str, str]]:
    payload = deepcopy(coverage or {}) if isinstance(coverage, dict) else {}
    inputs = normalize_coverage_inputs(payload.get("inputs", []))
    legacy_path = str(payload.get("artifact_path", "")).strip()
    if inputs or not legacy_path:
        return inputs

    inferred = "xml" if legacy_path.endswith(".xml") else "lcov"
    return [{"format": inferred, "name": "default", "path": legacy_path}]


def normalize_java_setup(raw_java: Any) -> Dict[str, Any]:
    if isinstance(raw_java, str):
        raw_java = {"distribution": "temurin", "version": raw_java}
    java = deepcopy(raw_java) if isinstance(raw_java, dict) else {}
    return {
        "distribution": str(java.get("distribution", "")).strip(),
        "version": str(java.get("version", "")).strip(),
    }


def normalize_coverage_setup(raw_setup: Any) -> Dict[str, Any]:
    setup = deepcopy(raw_setup) if isinstance(raw_setup, dict) else {}
    return {
        "python": str(setup.get("python", "")).strip(),
        "node": str(setup.get("node", "")).strip(),
        "go": str(setup.get("go", "")).strip(),
        "dotnet": str(setup.get("dotnet", "")).strip(),
        "rust": bool(setup.get("rust", False)),
        "system_packages": dedupe_strings(setup.get("system_packages", [])),
        "java": normalize_java_setup(setup.get("java", {})),
    }


def normalize_coverage_assert_mode(raw_assert_mode: Any) -> Dict[str, str]:
    if isinstance(raw_assert_mode, str):
        raw_assert_mode = {"default": raw_assert_mode}
    if not isinstance(raw_assert_mode, dict):
        return {"default": "enforce"}

    resolved = {
        str(key): text
        for key, value in raw_assert_mode.items()
        if (text := str(value or "").strip())
    }
    return {"default": "enforce", **resolved}


def normalize_coverage(raw: Mapping[str, Any] | None) -> Dict[str, Any]:
    coverage = deepcopy(raw or {}) if isinstance(raw, dict) else {}
    inputs = infer_coverage_inputs(coverage)
    coverage["runner"] = str(coverage.get("runner", "ubuntu-latest")).strip() or "ubuntu-latest"
    coverage["shell"] = str(coverage.get("shell", "bash")).strip() or "bash"
    coverage["command"] = str(coverage.get("command", "")).strip()
    coverage["inputs"] = inputs
    coverage["require_sources"] = dedupe_strings(coverage.get("require_sources", []))
    coverage["min_percent"] = float(coverage.get("min_percent", 100.0))
    coverage["assert_mode"] = normalize_coverage_assert_mode(coverage.get("assert_mode", {}))
    coverage["evidence_note"] = str(coverage.get("evidence_note", "")).strip()
    coverage["setup"] = normalize_coverage_setup(coverage.get("setup", {}))
    return coverage


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
