#!/usr/bin/env python3
"""Render managed security-baseline files for one governed repository."""

from __future__ import absolute_import

import argparse
from pathlib import Path
import re
import sys
from typing import Any, Dict, List

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


def render_codeql_wrapper(*, platform_release_sha: str) -> str:
    """Return the governed repo CodeQL wrapper workflow."""
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
            f"    uses: Prekzursil/quality-zero-platform/.github/workflows/reusable-codeql.yml@{platform_release_sha}",
            "    with:",
            "      repo_slug: ${{ github.repository }}",
            "      event_name: ${{ github.event_name }}",
            "      sha: ${{ github.event.pull_request.head.sha || github.sha }}",
            "      platform_repository: Prekzursil/quality-zero-platform",
            "      platform_ref: main",
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


def render_security_policy(profile: Dict[str, Any]) -> str:
    """Return the governed SECURITY.md content for one repo."""
    slug = profile["slug"]
    return "\n".join(
        [
            "# Security Policy",
            "",
            "## Supported Versions",
            "",
            "Security fixes are applied to the `main` branch.",
            "",
            "| Version | Supported |",
            "| --- | --- |",
            "| `main` | :white_check_mark: |",
            "| Other branches/tags | :x: |",
            "",
            "## Reporting a Vulnerability",
            "",
            "Please do **not** open public GitHub issues for undisclosed security findings.",
            "",
            "Use GitHub Private Vulnerability Reporting for this repository:",
            f"<https://github.com/{slug}/security/advisories/new>",
            "",
            "If private advisory reporting is unavailable, contact the maintainer privately on GitHub (`@Prekzursil`).",
            "",
            "When reporting, include:",
            "",
            "- the affected component, file, workflow, or dependency",
            "- the exact commit, branch, or release if known",
            "- clear reproduction or proof-of-concept steps",
            "- impact details covering confidentiality, integrity, or availability",
            "- any suggested mitigation if known",
            "",
            "## Disclosure Expectations",
            "",
            "- Initial acknowledgment: best effort within 3 business days.",
            "- Triage update: best effort within 7 business days.",
            "- Coordinated disclosure is expected; please allow time to investigate and patch before public disclosure.",
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
        render_codeql_wrapper(platform_release_sha=platform_release_sha),
    )
    _write_text(
        repo_root / ".github" / "dependabot.yml",
        render_dependabot_config(profile),
    )
    _write_text(repo_root / "SECURITY.md", render_security_policy(profile))


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
