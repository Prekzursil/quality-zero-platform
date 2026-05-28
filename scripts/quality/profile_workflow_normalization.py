"""Workflow-tooling normalisers split from :mod:`profile_normalization`.

Houses the normalisers for the workflow-adjacent profile sections (Codex
environment, CodeQL, Dependabot) so the core ``profile_normalization`` module
stays bounded in file-level complexity. The public names are re-exported from
``profile_normalization`` to preserve the historical import surface.
"""

from __future__ import absolute_import

from copy import deepcopy
from typing import Any, Dict, Mapping

from scripts.quality.profile_coverage_normalization import normalize_coverage_setup
from scripts.quality.string_helpers import dedupe_strings


def normalize_codex_environment(
    raw: Mapping[str, Any] | None, *, verify_command: str
) -> Dict[str, Any]:
    """Handle normalize codex environment."""
    payload = deepcopy(raw or {}) if isinstance(raw, dict) else {}
    return {
        "mode": str(payload.get("mode", "automatic")).strip() or "automatic",
        "verify_command": str(payload.get("verify_command", verify_command)).strip()
        or verify_command,
        "auth_file": str(payload.get("auth_file", "~/.codex/auth.json")).strip()
        or "~/.codex/auth.json",
        "network_profile": str(payload.get("network_profile", "unrestricted")).strip()
        or "unrestricted",
        "methods": str(payload.get("methods", "all")).strip() or "all",
        "runner_labels": dedupe_strings(
            payload.get("runner_labels", ["self-hosted", "codex-trusted"])
        ),
    }


def normalize_codeql(raw: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Normalize CodeQL workflow settings."""
    payload = deepcopy(raw or {}) if isinstance(raw, dict) else {}
    return {
        "enabled": bool(payload.get("enabled", True)),
        "languages": dedupe_strings(payload.get("languages", [])),
        "runner": str(payload.get("runner", "ubuntu-latest")).strip()
        or "ubuntu-latest",
        "build_mode": str(payload.get("build_mode", "none")).strip() or "none",
        "setup": normalize_coverage_setup(payload.get("setup", {})),
    }


def normalize_dependabot(raw: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Normalize Dependabot update settings."""
    payload = deepcopy(raw or {}) if isinstance(raw, dict) else {}
    updates = []
    for item in payload.get("updates", []):
        if not isinstance(item, dict):
            continue
        ecosystem = str(item.get("ecosystem", "")).strip()
        directory = str(item.get("directory", "")).strip()
        if not ecosystem or not directory:
            continue
        updates.append(
            {
                "ecosystem": ecosystem,
                "directory": directory,
            }
        )
    return {
        "enabled": bool(payload.get("enabled", True)),
        "updates": updates,
        "open_pull_requests_limit": int(payload.get("open_pull_requests_limit", 10)),
        "schedule_interval": str(payload.get("schedule_interval", "weekly")).strip()
        or "weekly",
        "labels": dedupe_strings(
            payload.get("labels", ["dependencies", "type:chore", "area:ci"])
        ),
    }
