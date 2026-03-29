from __future__ import absolute_import

from copy import deepcopy
from typing import Any, Dict, Set
from urllib.parse import quote

from scripts.security_helpers import normalize_https_url


def normalize_visual_lane(raw: Dict[str, Any]) -> Dict[str, str]:
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
    if not vendor.get(key):
        vendor[key] = normalize_https_url(url, allowed_host_suffixes=allowed_host_suffixes)


def _finalize_sonar_vendor(
    vendors: Dict[str, Any],
    *,
    owner: str = "",
    repo_name: str = "",
) -> None:
    sonar = vendors.setdefault("sonar", {})
    project_key = str(sonar.get("project_key", "")).strip()
    if not project_key and bool(sonar.get("project_key_from_repo_slug")):
        resolved_owner = owner or str(vendors.get("codacy", {}).get("owner", "")).strip()
        resolved_repo_name = repo_name or str(vendors.get("codacy", {}).get("repo", "")).strip()
        project_key = f"{resolved_owner}_{resolved_repo_name}".strip("_")
    if not project_key:
        separator = str(sonar.get("project_key_separator", "_")).strip() or "_"
        parts = [
            str(item).strip()
            for item in sonar.get("project_key_parts", [])
            if str(item).strip()
        ]
        if parts:
            project_key = separator.join(parts)
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


def _finalize_codacy_vendor(vendors: Dict[str, Any], *, owner: str, repo_name: str) -> None:
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


def _finalize_codecov_vendor(vendors: Dict[str, Any], *, owner: str, repo_name: str) -> None:
    _ensure_vendor_url(
        vendors.setdefault("codecov", {}),
        "dashboard_url",
        f"https://app.codecov.io/gh/{quote(owner, safe='')}/{quote(repo_name, safe='')}",
        allowed_host_suffixes={"codecov.io"},
    )


def _finalize_qlty_vendor(vendors: Dict[str, Any], *, owner: str, repo_name: str) -> None:
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


def _finalize_visual_vendors(profile: Dict[str, Any], vendors: Dict[str, Any]) -> None:
    if not profile.get("visual_pair_required"):
        return

    repo_name = profile["repo_name"]
    chromatic = vendors.setdefault("chromatic", {})
    chromatic.setdefault("project_name", repo_name)
    chromatic.setdefault("token_secret", "CHROMATIC_PROJECT_TOKEN")
    chromatic.setdefault("local_env_var", f"CHROMATIC_PROJECT_TOKEN_{_provider_env_suffix(repo_name)}")

    applitools = vendors.setdefault("applitools", {})
    applitools.setdefault("project_name", repo_name)


def _finalize_passthrough_vendors(vendors: Dict[str, Any]) -> None:
    vendors.setdefault("deepscan", {}).setdefault("open_issues_url_var", "DEEPSCAN_OPEN_ISSUES_URL")
    sentry = vendors.setdefault("sentry", {})
    sentry.setdefault("org_var", "SENTRY_ORG")
    sentry.setdefault("project_vars", ["SENTRY_PROJECT"])
    vendors.setdefault("chromatic", {}).setdefault("status_context", "Chromatic Playwright")
    vendors.setdefault("applitools", {}).setdefault("status_context", "Applitools Visual")


def finalize_vendors(profile: Dict[str, Any], vendor_source: Dict[str, Any]) -> Dict[str, Any]:
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
