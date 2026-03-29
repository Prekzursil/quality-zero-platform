"""Vendor finalization helpers for control-plane profiles."""

from __future__ import absolute_import

from copy import deepcopy
from typing import Any, Dict, List, Set
from urllib.parse import quote

from scripts.security_helpers import normalize_https_url


def normalize_visual_lane(raw: Dict[str, Any]) -> Dict[str, str]:
    """Normalize a visual lane payload into the control-plane schema."""
    payload = deepcopy(raw or {})
    return {
        "kind": str(payload.get("kind", "none")).strip() or "none",
    }


def _ensure_vendor_url(
    vendor: Dict[str, Any],
    key: str,
    url: str,
    *,
    allowed_host_suffixes: Set[str],
) -> None:
    """Populate a vendor URL only when the field is currently empty."""
    if not vendor.get(key):
        vendor[key] = normalize_https_url(
            url,
            allowed_host_suffixes=allowed_host_suffixes,
        )


def _normalize_sonar_project_key_parts(raw_parts: Any) -> List[str]:
    """Return the non-empty Sonar project-key parts in first-seen order."""
    return [str(item).strip() for item in raw_parts if str(item).strip()]


def _resolve_sonar_project_key(
    vendors: Dict[str, Any],
    sonar: Dict[str, Any],
    *,
    owner: str,
    repo_name: str,
) -> str:
    """Resolve the Sonar project key from explicit or derived inputs."""
    project_key = str(sonar.get("project_key", "")).strip()
    if project_key:
        return project_key

    if bool(sonar.get("project_key_from_repo_slug")):
        resolved_owner = (
            owner or str(vendors.get("codacy", {}).get("owner", "")).strip()
        )
        resolved_repo_name = (
            repo_name or str(vendors.get("codacy", {}).get("repo", "")).strip()
        )
        return f"{resolved_owner}_{resolved_repo_name}".strip("_")

    separator = str(sonar.get("project_key_separator", "_")).strip() or "_"
    parts = _normalize_sonar_project_key_parts(sonar.get("project_key_parts", []))
    if parts:
        return separator.join(parts)
    return ""


def _finalize_sonar_vendor(
    vendors: Dict[str, Any],
    *,
    owner: str = "",
    repo_name: str = "",
) -> None:
    """Finalize Sonar vendor settings and derived dashboard metadata."""
    sonar = vendors.setdefault("sonar", {})
    project_key = _resolve_sonar_project_key(
        vendors,
        sonar,
        owner=owner,
        repo_name=repo_name,
    )
    if project_key:
        sonar["project_key"] = project_key
    project_key = str(sonar.get("project_key", "")).strip()
    if project_key:
        _ensure_vendor_url(
            sonar,
            "dashboard_url",
            f"https://sonarcloud.io/project/overview?id={quote(project_key, safe='')}",
            allowed_host_suffixes={"sonarcloud.io"},
        )


def _finalize_codacy_vendor(
    vendors: Dict[str, Any],
    *,
    owner: str,
    repo_name: str,
) -> None:
    """Finalize Codacy vendor settings and dashboard URL."""
    codacy = vendors.setdefault("codacy", {})
    codacy.setdefault("provider", "gh")
    codacy.setdefault("owner", owner)
    codacy.setdefault("repo", repo_name)
    codacy.setdefault("profile_mode", "defaults_all_languages")
    _ensure_vendor_url(
        codacy,
        "dashboard_url",
        (
            "https://app.codacy.com/gh/"
            f"{quote(owner, safe='')}/{quote(repo_name, safe='')}/dashboard"
        ),
        allowed_host_suffixes={"codacy.com"},
    )


def _finalize_codecov_vendor(
    vendors: Dict[str, Any],
    *,
    owner: str,
    repo_name: str,
) -> None:
    """Finalize Codecov vendor settings and dashboard URL."""
    _ensure_vendor_url(
        vendors.setdefault("codecov", {}),
        "dashboard_url",
        (
            "https://app.codecov.io/gh/"
            f"{quote(owner, safe='')}/{quote(repo_name, safe='')}"
        ),
        allowed_host_suffixes={"codecov.io"},
    )


def _finalize_qlty_vendor(
    vendors: Dict[str, Any],
    *,
    owner: str,
    repo_name: str,
) -> None:
    """Finalize QLTY vendor metadata and dashboard URL."""
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
        (
            "https://qlty.sh/gh/"
            f"{quote(owner, safe='')}/projects/{quote(repo_name, safe='')}"
        ),
        allowed_host_suffixes={"qlty.sh"},
    )


def _provider_env_suffix(repo_name: str) -> str:
    """Convert a repository name into a stable environment-variable suffix."""
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


def _finalize_visual_vendors(profile: Dict[str, Any], vendors: Dict[str, Any]) -> None:
    """Finalize vendor settings for repositories that require visual pairing."""
    if not profile.get("visual_pair_required"):
        return

    repo_name = profile["repo_name"]
    chromatic = vendors.setdefault("chromatic", {})
    chromatic.setdefault("project_name", repo_name)
    chromatic.setdefault("token_secret", "CHROMATIC_PROJECT_TOKEN")
    chromatic.setdefault(
        "local_env_var",
        f"CHROMATIC_PROJECT_TOKEN_{_provider_env_suffix(repo_name)}",
    )

    applitools = vendors.setdefault("applitools", {})
    applitools.setdefault("project_name", repo_name)


def _finalize_passthrough_vendors(vendors: Dict[str, Any]) -> None:
    """Populate passthrough vendor defaults used by downstream checks."""
    vendors.setdefault(
        "deepscan",
        {},
    ).setdefault("open_issues_url_var", "DEEPSCAN_OPEN_ISSUES_URL")
    sentry = vendors.setdefault("sentry", {})
    sentry.setdefault("org_var", "SENTRY_ORG")
    sentry.setdefault("project_vars", ["SENTRY_PROJECT"])
    vendors.setdefault("chromatic", {}).setdefault(
        "status_context",
        "Chromatic Playwright",
    )
    vendors.setdefault("applitools", {}).setdefault(
        "status_context",
        "Applitools Visual",
    )


def finalize_vendors(
    profile: Dict[str, Any],
    vendor_source: Dict[str, Any],
) -> Dict[str, Any]:
    """Finalize all vendor metadata for a profile."""
    owner = profile["owner"]
    repo_name = profile["repo_name"]
    vendors = deepcopy(vendor_source)

    _finalize_codacy_vendor(vendors, owner=owner, repo_name=repo_name)
    _finalize_sonar_vendor(vendors, owner=owner, repo_name=repo_name)
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
