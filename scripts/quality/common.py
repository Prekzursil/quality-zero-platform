"""Common."""

from __future__ import absolute_import

import json
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping

from scripts.quality.string_helpers import dedupe_strings as _dedupe_strings
from scripts.security_helpers import load_json_https

DEFAULT_COVERAGE_JSON = "coverage-100/coverage.json"
DEFAULT_COVERAGE_MD = "coverage-100/coverage.md"
NONE_BULLET = "- None"
GITHUB_API_BASE = "https://api.github.com"


@dataclass(frozen=True, slots=True)
class ReportSpec:
    """Report Spec."""

    out_json: str
    out_md: str
    default_json: str
    default_md: str
    render_md: Callable[[Mapping[str, Any]], str]


def utc_timestamp() -> str:
    """Handle utc timestamp."""
    return datetime.now(timezone.utc).isoformat()


def github_commit_status_payload(repo: str, sha: str, token: str) -> Dict[str, Any]:
    """Fetch the GitHub status payload for one commit."""
    payload, _ = load_json_https(
        f"{GITHUB_API_BASE}/repos/{repo}/commits/{sha}/status",
        allowed_hosts={"api.github.com"},
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "quality-zero-platform",
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected GitHub status response payload")
    return payload


def dedupe_strings(items: Iterable[str | None]) -> List[str]:
    """Preserve the historical import surface for shared string deduping."""
    return _dedupe_strings(items)


def _ensure_within_root(path: Path, root: Path) -> Path:
    """Return ``path`` when it stays within ``root``."""
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Output path escapes workspace root: {path}") from exc
    return path


def safe_output_path(raw: str, fallback: str, base: Path | None = None) -> Path:
    """Handle safe output path."""
    root = (base or Path.cwd()).resolve()
    candidate = Path((raw or "").strip() or fallback)
    resolved = (
        candidate.resolve(strict=False)
        if candidate.is_absolute()
        else (root / candidate).resolve(strict=False)
    )
    return _ensure_within_root(resolved, root)


def _raise_write_report_type_error(message: str) -> None:
    """Handle raise write report type error."""
    raise TypeError(message)


def _validate_report_spec_args(args: Any, kwargs: Any) -> ReportSpec | None:
    """Handle validate report spec args."""
    if args and isinstance(args[0], ReportSpec):
        if len(args) != 1 or kwargs:
            _raise_write_report_type_error(
                "write_report expects a ReportSpec or legacy keyword arguments"
            )
        return args[0]

    if args:
        _raise_write_report_type_error(
            "write_report expects a ReportSpec or legacy keyword arguments"
        )
    return None


def _pop_report_kwargs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Handle pop report kwargs."""
    required = ("out_json", "out_md", "default_json", "default_md", "render_md")
    missing = [key for key in required if key not in kwargs]
    if missing:
        _raise_write_report_type_error(
            f"Missing required report parameter: {missing[0]}"
        )

    values = {key: kwargs.pop(key) for key in required}
    if kwargs:
        _raise_write_report_type_error(
            f"Unexpected write_report parameters: {', '.join(sorted(kwargs))}"
        )
    return values


def _resolve_report_spec(*args: Any, **kwargs: Any) -> ReportSpec:
    """Handle resolve report spec."""
    spec = _validate_report_spec_args(args, kwargs)
    if spec is not None:
        return spec
    return _report_spec_from_kwargs(_pop_report_kwargs(kwargs))


def _report_spec_from_kwargs(values: Mapping[str, Any]) -> ReportSpec:
    """Handle report spec from kwargs."""
    return ReportSpec(
        out_json=str(values["out_json"]),
        out_md=str(values["out_md"]),
        default_json=str(values["default_json"]),
        default_md=str(values["default_md"]),
        render_md=values["render_md"],
    )


def _write_workspace_text(path: Path, content: str, *, root: Path) -> None:
    """Write UTF-8 text after revalidating the workspace-relative target."""
    target = _ensure_within_root(path.resolve(strict=False), root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        handle.write(content)


def write_report(payload: Mapping[str, Any], *args: Any, **kwargs: Any) -> int:
    """Handle write report."""
    spec = _resolve_report_spec(*args, **kwargs)
    workspace_root = Path.cwd().resolve()
    try:
        json_path = safe_output_path(
            spec.out_json,
            spec.default_json,
            base=workspace_root,
        )
        md_path = safe_output_path(
            spec.out_md,
            spec.default_md,
            base=workspace_root,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    markdown_text = spec.render_md(payload)
    _write_workspace_text(json_path, json_text, root=workspace_root)
    _write_workspace_text(md_path, markdown_text, root=workspace_root)
    print(markdown_text, end="")
    return 0


def normalize_required_contexts(raw: Mapping[str, Any] | None) -> Dict[str, List[str]]:
    """Handle normalize required contexts."""
    from scripts.quality.profile_normalization import (
        normalize_required_contexts as impl,
    )

    return impl(raw)


def merge_required_contexts(
    base: Mapping[str, Any] | None, overlay: Mapping[str, Any] | None
) -> Dict[str, List[str]]:
    """Handle merge required contexts."""
    from scripts.quality.profile_normalization import merge_required_contexts as impl

    return impl(base, overlay)


def normalize_coverage_inputs(raw_inputs: Any) -> List[Dict[str, str]]:
    """Handle normalize coverage inputs."""
    from scripts.quality.profile_normalization import normalize_coverage_inputs as impl

    return impl(raw_inputs)


def infer_coverage_inputs(coverage: Mapping[str, Any] | None) -> List[Dict[str, str]]:
    """Handle infer coverage inputs."""
    from scripts.quality.profile_normalization import infer_coverage_inputs as impl

    return impl(coverage)


def normalize_java_setup(raw_java: Any) -> Dict[str, Any]:
    """Handle normalize java setup."""
    from scripts.quality.profile_normalization import normalize_java_setup as impl

    return impl(raw_java)


def normalize_coverage_setup(raw_setup: Any) -> Dict[str, Any]:
    """Handle normalize coverage setup."""
    from scripts.quality.profile_normalization import normalize_coverage_setup as impl

    return impl(raw_setup)


def normalize_coverage_assert_mode(raw_assert_mode: Any) -> Dict[str, str]:
    """Handle normalize coverage assert mode."""
    from scripts.quality.profile_normalization import (
        normalize_coverage_assert_mode as impl,
    )

    return impl(raw_assert_mode)


def normalize_coverage(raw: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Handle normalize coverage."""
    from scripts.quality.profile_normalization import normalize_coverage as impl

    return impl(raw)


def normalize_issue_policy(raw: Mapping[str, Any] | str | None) -> Dict[str, str]:
    """Handle normalize issue policy."""
    from scripts.quality.profile_normalization import normalize_issue_policy as impl

    return impl(raw)


def normalize_deps(raw: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Handle normalize deps."""
    from scripts.quality.profile_normalization import normalize_deps as impl

    return impl(raw)


def normalize_codex_environment(
    raw: Mapping[str, Any] | None, *, verify_command: str
) -> Dict[str, Any]:
    """Handle normalize codex environment."""
    from scripts.quality.profile_normalization import (
        normalize_codex_environment as impl,
    )

    return impl(raw, verify_command=verify_command)


def finalize_vendors(profile: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Handle finalize vendors."""
    payload = deepcopy(profile or {}) if isinstance(profile, dict) else {}
    return _deep_merge(payload.get("vendors", {}), payload.get("providers", {}))


def _deep_merge(base: Any, overlay: Any) -> Any:
    """Handle deep merge."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = deepcopy(base)
        for key, value in overlay.items():
            merged[key] = (
                _deep_merge(merged[key], value) if key in merged else deepcopy(value)
            )
        return merged
    return deepcopy(overlay)
