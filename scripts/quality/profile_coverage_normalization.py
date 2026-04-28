"""Normalize coverage profile blocks for shared quality workflows."""

from __future__ import absolute_import

import contextlib
import re
from copy import deepcopy
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from scripts.quality.string_helpers import dedupe_strings

_LCOV_SRC_RE = re.compile(
    r"(?P<prefix>.+?)/coverage/(?:lcov|lcov\.info|lcov-report).*",
    re.IGNORECASE,
)
_COV_ARG_RE = re.compile(r"--cov(?:=|\s+)(?P<value>[A-Za-z0-9_./-]+)")
_GCOVR_FILTER_RE = re.compile(r"--filter\s+'\.*/(?P<value>[A-Za-z0-9_./-]+)/\.\*'")


def _normalize_source_hint(raw: str) -> str:
    """Normalize a source-path hint inferred from coverage configuration."""
    value = str(raw or "").strip().replace("\\", "/")
    if not value:
        return ""
    if "/" not in value and "." in value:
        value = value.replace(".", "/") + ".py"
    if value.endswith((".py", ".js", ".ts", ".tsx", ".jsx")):
        return value
    return value.rstrip("/") + "/"


def _extract_cov_hints(command: str) -> List[str]:
    """Return source hints inferred from pytest-style `--cov` arguments."""
    inferred: List[str] = []
    for match in _COV_ARG_RE.finditer(command):
        hint = _normalize_source_hint(match.group("value"))
        if hint:
            inferred.append(hint)
    return inferred


def _extract_gcovr_hints(command: str) -> List[str]:
    """Return source hints inferred from gcovr filter arguments."""
    inferred: List[str] = []
    for match in _GCOVR_FILTER_RE.finditer(command):
        hint = _normalize_source_hint(match.group("value"))
        if hint:
            inferred.append(hint)
    if "src/.*" in command or "src/.*'" in command or 'src/.*"' in command:
        inferred.append("src/")
    return inferred


def _extract_lcov_hint_from_inputs(inputs: Sequence[Mapping[str, Any]]) -> List[str]:
    """Infer source roots from LCOV artifact input paths when available."""
    inferred: List[str] = []
    for item in inputs:
        path = str(item.get("path", "")).replace("\\", "/").strip()
        lcov_match = _LCOV_SRC_RE.match(path)
        if lcov_match:
            inferred.append(_normalize_source_hint(f"{lcov_match.group('prefix')}/src"))
    return inferred


def infer_required_sources(raw_coverage: Mapping[str, Any] | None) -> List[str]:
    """Infer required source roots from coverage commands and input artifacts."""
    coverage = deepcopy(raw_coverage or {}) if isinstance(raw_coverage, dict) else {}
    command = str(coverage.get("command", "")).strip()
    inputs = infer_coverage_inputs(coverage)
    inferred = [
        *_extract_cov_hints(command),
        *_extract_gcovr_hints(command),
        *_extract_lcov_hint_from_inputs(inputs),
    ]
    return dedupe_strings(inferred)


def _add_optional_input_fields(item: Mapping[str, Any], dest: Dict[str, Any]) -> None:
    """Copy optional ``flag``/``sources``/``min_percent`` from ``item``."""
    if "flag" in item:
        dest["flag"] = str(item["flag"]).strip()
    if "sources" in item and isinstance(item["sources"], list):
        dest["sources"] = [
            str(s).strip() for s in item["sources"] if str(s).strip()
        ]
    if "min_percent" in item:
        # Drop the field on parse error so downstream consumers fall
        # back to the schema default rather than a malformed value.
        with contextlib.suppress(TypeError, ValueError):
            dest["min_percent"] = float(item["min_percent"])


def _normalize_one_input(item: Any) -> Dict[str, Any] | None:
    """Return one normalized coverage input, or ``None`` to drop the row."""
    if not isinstance(item, dict):
        return None
    normalized: Dict[str, Any] = {
        "format": str(item.get("format", "")).strip().lower(),
        "name": str(item.get("name", "")).strip(),
        "path": str(item.get("path", "")).strip(),
    }
    if (
        normalized["format"] not in {"xml", "lcov"}
        or not normalized["name"]
        or not normalized["path"]
    ):
        return None
    _add_optional_input_fields(item, normalized)
    return normalized


def normalize_coverage_inputs(raw_inputs: Any) -> List[Dict[str, Any]]:
    """Normalize raw coverage input mappings into the shared schema.

    Always emits ``format``, ``name``, ``path``. Optional fields are
    passed through ONLY when present on the raw input:

    * ``flag``       — Phase 2 Codecov per-flag upload identifier.
                       Required by reusable-codecov-analytics.yml.
    * ``sources``    — list of source-root globs used by drift-sync
                       templates (``codecov.yml.j2`` in particular).
    * ``min_percent``— per-input coverage threshold override.

    Filtering rule unchanged: drop non-dict entries and entries
    without xml/lcov format or with empty name/path.
    """
    if not isinstance(raw_inputs, list):
        return []
    normalized_items: List[Dict[str, Any]] = []
    for item in raw_inputs:
        normalized = _normalize_one_input(item)
        if normalized is not None:
            normalized_items.append(normalized)
    return normalized_items


def infer_coverage_inputs(coverage: Mapping[str, Any] | None) -> List[Dict[str, str]]:
    """Return normalized coverage inputs.

    Fall back to a legacy artifact path when explicit inputs are absent.
    """
    payload = deepcopy(coverage or {}) if isinstance(coverage, dict) else {}
    inputs = normalize_coverage_inputs(payload.get("inputs", []))
    legacy_path = str(payload.get("artifact_path", "")).strip()
    if inputs or not legacy_path:
        return inputs

    inferred = "xml" if legacy_path.endswith(".xml") else "lcov"
    return [{"format": inferred, "name": "default", "path": legacy_path}]


def normalize_java_setup(raw_java: Any) -> Dict[str, Any]:
    """Normalize optional Java toolchain setup for coverage runs."""
    if isinstance(raw_java, str):
        raw_java = {"distribution": "temurin", "version": raw_java}
    java = deepcopy(raw_java) if isinstance(raw_java, dict) else {}
    return {
        "distribution": str(java.get("distribution", "")).strip(),
        "version": str(java.get("version", "")).strip(),
    }


def normalize_coverage_setup(raw_setup: Any) -> Dict[str, Any]:
    """Normalize language/runtime setup needed before coverage collection."""
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
    """Normalize per-component coverage assertion modes."""
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


def _normalize_branch_min_percent(raw_branch_min_percent: Any) -> float | None:
    """Normalize optional minimum branch coverage into a float threshold."""
    if raw_branch_min_percent in {"", None}:
        return None
    try:
        return float(raw_branch_min_percent)
    except (TypeError, ValueError):
        return None


def _resolve_required_sources(coverage: Dict[str, Any]) -> Tuple[List[str], str]:
    """Resolve required coverage sources and whether they were explicit or inferred."""
    require_sources = dedupe_strings(coverage.get("require_sources", []))
    require_sources_mode = (
        "explicit"
        if require_sources
        else str(coverage.get("require_sources_mode", "infer")).strip() or "infer"
    )
    if require_sources_mode == "infer" and not require_sources:
        require_sources = infer_required_sources(coverage)
    return require_sources, require_sources_mode


def normalize_coverage(raw: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Normalize the full coverage block consumed by the shared quality gates."""
    coverage = deepcopy(raw or {}) if isinstance(raw, dict) else {}
    inputs = infer_coverage_inputs(coverage)
    require_sources, require_sources_mode = _resolve_required_sources(coverage)
    resolved_shell = coverage.get("command_shell", coverage.get("shell", "bash"))
    coverage.pop("command_shell", None)
    coverage["runner"] = (
        str(coverage.get("runner", "ubuntu-latest")).strip() or "ubuntu-latest"
    )
    coverage["shell"] = str(resolved_shell).strip() or "bash"
    coverage["command"] = str(coverage.get("command", "")).strip()
    coverage["inputs"] = inputs
    coverage["require_sources"] = require_sources
    coverage["require_sources_mode"] = require_sources_mode
    coverage["min_percent"] = float(coverage.get("min_percent", 100.0))
    coverage["branch_min_percent"] = _normalize_branch_min_percent(
        coverage.get("branch_min_percent")
    )
    coverage["assert_mode"] = normalize_coverage_assert_mode(
        coverage.get("assert_mode", {})
    )
    coverage["evidence_note"] = str(coverage.get("evidence_note", "")).strip()
    coverage["setup"] = normalize_coverage_setup(coverage.get("setup", {}))
    return coverage
