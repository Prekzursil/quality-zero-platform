"""Common."""

from __future__ import absolute_import

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Dict, Iterable, List, Mapping

from scripts.quality.report_writer import (  # noqa: F401  pylint: disable=unused-import
    ReportSpec,
    _ensure_within_root,
    _resolve_report_spec,
    safe_output_path,
    write_report,
)
from scripts.quality.string_helpers import dedupe_strings as _dedupe_strings
from scripts.security_helpers import load_json_https

DEFAULT_COVERAGE_JSON = "coverage-100/coverage.json"
DEFAULT_COVERAGE_MD = "coverage-100/coverage.md"
NONE_BULLET = "- None"
GITHUB_API_BASE = "https://api.github.com"


def utc_timestamp() -> str:
    """Handle utc timestamp."""
    return datetime.now(UTC).isoformat()


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
