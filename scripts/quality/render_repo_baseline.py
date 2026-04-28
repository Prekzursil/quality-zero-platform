#!/usr/bin/env python3
"""Render managed security-baseline files for one governed repository."""

from __future__ import absolute_import

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml  # type: ignore[import-untyped]

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.control_plane import load_inventory, load_repo_profile

LEGACY_ZERO_WORKFLOW_FILES = (
    "coverage-100.yml",
    "codacy-zero.yml",
    "deepscan-zero.yml",
    "semgrep-zero.yml",
    "sentry-zero.yml",
    "sonar-zero.yml",
    "qlty-zero.yml",
)


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Render managed CodeQL, Dependabot, and SECURITY.md files."
    )
    parser.add_argument("--inventory", default="")
    parser.add_argument("--repo-slug", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--platform-release-sha", default="main")
    return parser.parse_args()


def _sanitize_group_name(ecosystem: str, directory: str) -> str:
    """Return a stable Dependabot group name for one update entry."""
    directory_slug = directory.strip("/") or "root"
    base = f"{ecosystem}-{directory_slug}-patch-minor"
    sanitized = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return sanitized or f"{ecosystem}-patch-minor"


def _codeql_wrapper_uses_line(is_self_repo: bool, platform_release_sha: str) -> str:
    """Pick the ``uses:`` line for the codeql wrapper (self-call vs external)."""
    if is_self_repo:
        return "    uses: ./.github/workflows/reusable-codeql.yml"
    return (
        "    uses: Prekzursil/quality-zero-platform/"
        ".github/workflows/"
        "reusable-codeql.yml"
        f"@{platform_release_sha}"
    )


def _codeql_wrapper_platform_lines(is_self_repo: bool) -> Tuple[str, str]:
    """Pick (platform_repository, platform_ref) lines for codeql wrapper."""
    if is_self_repo:
        return (
            "      platform_repository: ${{ github.repository }}",
            "      platform_ref: ${{ github.event.pull_request.head.sha || github.sha }}",
        )
    return (
        "      platform_repository: Prekzursil/quality-zero-platform",
        "      platform_ref: main",
    )


def render_codeql_wrapper(*, repo_slug: str, platform_release_sha: str) -> str:
    """Return the governed repo CodeQL wrapper workflow."""
    is_self_repo = repo_slug == "Prekzursil/quality-zero-platform"
    uses_line = _codeql_wrapper_uses_line(is_self_repo, platform_release_sha)
    platform_repository, platform_ref = _codeql_wrapper_platform_lines(is_self_repo)
    return "\n".join(
        [
            "name: CodeQL",
            "",
            "permissions:",
            "  actions: read",
            "  contents: read",
            "  security-events: write",
            "",
            "on:",
            "  push:",
            "    branches: [main, master]",
            "  pull_request:",
            "    branches: [main, master]",
            "  merge_group:",
            "    types: [checks_requested]",
            "  schedule:",
            "    - cron: \"23 3 * * 1\"",
            "  workflow_dispatch:",
            "",
            "jobs:",
            "  codeql:",
            "    permissions:",
            "      actions: read",
            "      contents: read",
            "      security-events: write",
            uses_line,
            "    with:",
            "      repo_slug: ${{ github.repository }}",
            "      event_name: ${{ github.event_name }}",
            "      sha: ${{ github.event.pull_request.head.sha || github.sha }}",
            platform_repository,
            platform_ref,
            "",
        ]
    )


def render_dependabot_config(profile: Dict[str, Any]) -> str:
    """Return the managed Dependabot config for one repo."""
    dependabot = profile["dependabot"]
    updates = list(dependabot.get("updates", []))
    if not any(item.get("ecosystem") == "github-actions" for item in updates):
        updates.append({"ecosystem": "github-actions", "directory": "/"})

    payload: Dict[str, Any] = {"version": 2, "updates": []}
    for item in updates:
        ecosystem = str(item["ecosystem"]).strip()
        directory = str(item["directory"]).strip()
        group_name = _sanitize_group_name(ecosystem, directory)
        payload["updates"].append(
            {
                "package-ecosystem": ecosystem,
                "directory": directory,
                "schedule": {"interval": dependabot["schedule_interval"]},
                "open-pull-requests-limit": dependabot["open_pull_requests_limit"],
                "groups": {
                    group_name: {
                        "patterns": ["*"],
                        "update-types": ["patch", "minor"],
                    }
                },
                "ignore": [
                    {
                        "dependency-name": "*",
                        "update-types": ["version-update:semver-major"],
                    }
                ],
                "labels": list(dependabot["labels"]),
            }
        )

    return yaml.safe_dump(payload, sort_keys=False)


_SECURITY_POLICY_HEADER = (
    "# Security Policy\n\n"
    "## Supported Versions\n\n"
    "Security fixes are applied to the `main` branch.\n\n"
    "| Version | Supported |\n"
    "| --- | --- |\n"
    "| `main` | :white_check_mark: |\n"
    "| Other branches/tags | :x: |\n"
)

_SECURITY_POLICY_REPORT_GUIDANCE = (
    "When reporting, include:\n\n"
    "- the affected component, file, workflow, or dependency\n"
    "- the exact commit, branch, or release if known\n"
    "- clear reproduction or proof-of-concept steps\n"
    "- impact details covering confidentiality, integrity, or availability\n"
    "- any suggested mitigation if known\n"
)

_SECURITY_POLICY_DISCLOSURE = (
    "## Disclosure Expectations\n\n"
    "- Initial acknowledgment: best effort within 3 business days.\n"
    "- Triage update: best effort within 7 business days.\n"
    "- Coordinated disclosure is expected; please allow time to investigate"
    " and patch before public disclosure.\n"
)


def render_security_policy(profile: Dict[str, Any]) -> str:
    """Return the governed SECURITY.md content for one repo."""
    slug = profile["slug"]
    reporting_block = (
        "## Reporting a Vulnerability\n\n"
        "Please do **not** open public GitHub issues for undisclosed security findings.\n\n"
        "Use GitHub Private Vulnerability Reporting for this repository:\n"
        f"<https://github.com/{slug}/security/advisories/new>\n\n"
        "If private advisory reporting is unavailable, contact the maintainer privately"
        " on GitHub (`@Prekzursil`).\n"
    )
    return (
        _SECURITY_POLICY_HEADER
        + "\n"
        + reporting_block
        + "\n"
        + _SECURITY_POLICY_REPORT_GUIDANCE
        + "\n"
        + _SECURITY_POLICY_DISCLOSURE
        + "\n"
    )


def render_qlty_config() -> str:
    """Return the managed QLTY baseline config."""
    return "\n".join(
        [
            'config_version = "0"',
            "",
            "[[source]]",
            'name = "default"',
            "default = true",
            "",
            "[smells]",
            'mode = "block"',
            "",
        ]
    )


def _write_text(path: Path, text: str) -> None:
    """Write UTF-8 text with a trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _remove_legacy_zero_workflows(repo_root: Path) -> None:
    """Delete repo-local zero-gate workflows superseded by shared governance."""
    workflows_dir = repo_root / ".github" / "workflows"
    for filename in LEGACY_ZERO_WORKFLOW_FILES:
        path = workflows_dir / filename
        if path.exists():
            path.unlink()


def render_repo_baseline(
    *,
    profile: Dict[str, Any],
    repo_root: Path,
    platform_release_sha: str,
) -> None:
    """Render the managed baseline files into one repository checkout."""
    _remove_legacy_zero_workflows(repo_root)
    _write_text(
        repo_root / ".github" / "workflows" / "codeql.yml",
        render_codeql_wrapper(
            repo_slug=profile["slug"],
            platform_release_sha=platform_release_sha,
        ),
    )
    _write_text(
        repo_root / ".github" / "dependabot.yml",
        render_dependabot_config(profile),
    )
    _write_text(repo_root / "SECURITY.md", render_security_policy(profile))
    if bool(profile.get("enabled_scanners", {}).get("qlty", False)):
        _write_text(repo_root / ".qlty" / "qlty.toml", render_qlty_config())


def main() -> int:
    """Render files for one governed repository."""
    args = _parse_args()
    inventory = load_inventory(args.inventory) if args.inventory else load_inventory()
    profile = load_repo_profile(inventory, args.repo_slug)
    render_repo_baseline(
        profile=profile,
        repo_root=Path(args.repo_root).resolve(),
        platform_release_sha=str(args.platform_release_sha).strip() or "main",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
