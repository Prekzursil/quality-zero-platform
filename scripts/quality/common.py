from __future__ import absolute_import

import json
import os
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping

DEFAULT_COVERAGE_JSON = "coverage-100/coverage.json"
DEFAULT_COVERAGE_MD = "coverage-100/coverage.md"
NONE_BULLET = "- None"


@dataclass(frozen=True, slots=True)
class ReportSpec:
    out_json: str
    out_md: str
    default_json: str
    default_md: str
    render_md: Callable[[Mapping[str, Any]], str]


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def dedupe_strings(items: Iterable[str]) -> List[str]:
    normalized = (str(item or "").strip() for item in items)
    return list(dict.fromkeys(value for value in normalized if value))


def safe_output_path(raw: str, fallback: str, base: Path | None = None) -> Path:
    root = (base or Path.cwd()).resolve()
    candidate = Path((raw or "").strip() or fallback)
    resolved = candidate.resolve(strict=False) if candidate.is_absolute() else (root / candidate).resolve(strict=False)
    if os.path.commonpath([str(root), str(resolved)]) != str(root):
        raise ValueError(f"Output path escapes workspace root: {candidate}")
    return resolved


def _resolve_report_spec(*args: Any, **kwargs: Any) -> ReportSpec:
    if args and isinstance(args[0], ReportSpec):
        if len(args) != 1 or kwargs:
            raise TypeError("write_report expects a ReportSpec or legacy keyword arguments")
        return args[0]

    if args:
        raise TypeError("write_report expects a ReportSpec or legacy keyword arguments")

    required = ("out_json", "out_md", "default_json", "default_md", "render_md")
    missing = [key for key in required if key not in kwargs]
    if missing:
        raise TypeError(f"Missing required report parameter: {missing[0]}")

    out_json = kwargs.pop("out_json")
    out_md = kwargs.pop("out_md")
    default_json = kwargs.pop("default_json")
    default_md = kwargs.pop("default_md")
    render_md = kwargs.pop("render_md")
    if kwargs:
        raise TypeError(f"Unexpected write_report parameters: {', '.join(sorted(kwargs))}")
    return ReportSpec(
        out_json=str(out_json),
        out_md=str(out_md),
        default_json=str(default_json),
        default_md=str(default_md),
        render_md=render_md,
    )


def write_report(payload: Mapping[str, Any], *args: Any, **kwargs: Any) -> int:
    spec = _resolve_report_spec(*args, **kwargs)
    try:
        json_path = safe_output_path(spec.out_json, spec.default_json)
        md_path = safe_output_path(spec.out_md, spec.default_md)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(spec.render_md(payload), encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"), end="")
    return 0


def normalize_required_contexts(raw: Mapping[str, Any] | None) -> Dict[str, List[str]]:
    from scripts.quality.profile_normalization import normalize_required_contexts as impl

    return impl(raw)


def merge_required_contexts(base: Mapping[str, Any] | None, overlay: Mapping[str, Any] | None) -> Dict[str, List[str]]:
    from scripts.quality.profile_normalization import merge_required_contexts as impl

    return impl(base, overlay)


def normalize_coverage_inputs(raw_inputs: Any) -> List[Dict[str, str]]:
    from scripts.quality.profile_normalization import normalize_coverage_inputs as impl

    return impl(raw_inputs)


def infer_coverage_inputs(coverage: Mapping[str, Any] | None) -> List[Dict[str, str]]:
    from scripts.quality.profile_normalization import infer_coverage_inputs as impl

    return impl(coverage)


def normalize_java_setup(raw_java: Any) -> Dict[str, Any]:
    from scripts.quality.profile_normalization import normalize_java_setup as impl

    return impl(raw_java)


def normalize_coverage_setup(raw_setup: Any) -> Dict[str, Any]:
    from scripts.quality.profile_normalization import normalize_coverage_setup as impl

    return impl(raw_setup)


def normalize_coverage_assert_mode(raw_assert_mode: Any) -> Dict[str, str]:
    from scripts.quality.profile_normalization import normalize_coverage_assert_mode as impl

    return impl(raw_assert_mode)


def normalize_coverage(raw: Mapping[str, Any] | None) -> Dict[str, Any]:
    from scripts.quality.profile_normalization import normalize_coverage as impl

    return impl(raw)


def normalize_issue_policy(raw: Mapping[str, Any] | str | None) -> Dict[str, str]:
    from scripts.quality.profile_normalization import normalize_issue_policy as impl

    return impl(raw)


def normalize_deps(raw: Mapping[str, Any] | None) -> Dict[str, Any]:
    from scripts.quality.profile_normalization import normalize_deps as impl

    return impl(raw)


def normalize_codex_environment(raw: Mapping[str, Any] | None, *, verify_command: str) -> Dict[str, Any]:
    from scripts.quality.profile_normalization import normalize_codex_environment as impl

    return impl(raw, verify_command=verify_command)


def finalize_vendors(profile: Mapping[str, Any] | None) -> Dict[str, Any]:
    payload = deepcopy(profile or {}) if isinstance(profile, dict) else {}
    return _deep_merge(payload.get("vendors", {}), payload.get("providers", {}))


def _deep_merge(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = deepcopy(base)
        for key, value in overlay.items():
            merged[key] = _deep_merge(merged[key], value) if key in merged else deepcopy(value)
        return merged
    return deepcopy(overlay)
