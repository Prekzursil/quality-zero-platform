#!/usr/bin/env python3
"""Export profile."""

from __future__ import absolute_import

import argparse
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any, Dict, List

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.control_plane import (
    active_required_contexts,
    load_inventory,
    load_repo_profile,
)


def _parse_args() -> argparse.Namespace:
    """Handle parse args."""
    parser = argparse.ArgumentParser(
        description="Export a resolved control-plane profile for workflows."
    )
    parser.add_argument("--inventory", default="")
    parser.add_argument("--repo-slug", required=True)
    parser.add_argument("--event-name", default="pull_request")
    parser.add_argument("--output", default="")
    parser.add_argument("--out-json", default="")
    parser.add_argument("--github-output", default="")
    return parser.parse_args()


def _coverage_input_files(coverage: Dict[str, Any]) -> str:
    """Handle coverage input files."""
    return ",".join(
        str(PurePosixPath("repo") / PurePosixPath(str(item["path"]).replace("\\", "/")))
        for item in coverage.get("inputs", [])
    )


def _json_output(key: str, value: object) -> str:
    """Handle json output."""
    return f"{key}={json.dumps(value, separators=(',', ':'))}"


def _profile_output_lines(profile: Dict[str, Any], event_name: str) -> List[str]:
    """Handle profile output lines."""
    contexts = active_required_contexts(profile, event_name=event_name)
    codex_environment = profile.get("codex_environment", {})
    coverage_input_files = _coverage_input_files(profile.get("coverage", {}))
    enabled_scanners = profile.get("enabled_scanners", {})
    codecov_enabled = str(bool(enabled_scanners.get("codecov", False))).lower()
    qlty_enabled = str(bool(enabled_scanners.get("qlty", False))).lower()
    return [
        *_profile_identity_output_lines(profile),
        *_required_context_output_lines(profile, contexts),
        *_codex_environment_output_lines(codex_environment),
        *_coverage_output_lines(
            profile,
            coverage_input_files,
            codecov_enabled,
            qlty_enabled,
        ),
        _json_output("enabled_scanners_json", profile.get("enabled_scanners", {})),
        _json_output("vendors_json", profile.get("vendors", {})),
    ]


def _profile_identity_output_lines(profile: Dict[str, Any]) -> List[str]:
    """Return the base identity fields exported for one profile."""
    return [
        f"verify_command={profile['verify_command']}",
        f"default_branch={profile['default_branch']}",
        f"profile_id={profile['profile_id']}",
        f"stack={profile['stack']}",
        f"github_mutation_lane={profile['github_mutation_lane']}",
        f"codex_auth_lane={profile['codex_auth_lane']}",
        f"provider_ui_mode={profile['provider_ui_mode']}",
    ]


def _required_context_output_lines(
    profile: Dict[str, Any], contexts: List[str]
) -> List[str]:
    """Return the required-context fields exported for one profile."""
    return [
        _json_output("required_contexts_json", contexts),
        _json_output(
            "required_contexts_required_now_json",
            profile["required_contexts"]["required_now"],
        ),
        _json_output(
            "required_contexts_target_json", profile["required_contexts"]["target"]
        ),
        _json_output("required_secrets_json", profile["required_secrets"]),
        _json_output(
            "conditional_secrets_json", profile.get("conditional_secrets", [])
        ),
        _json_output("required_vars_json", profile["required_vars"]),
    ]


def _codex_environment_output_lines(codex_environment: Dict[str, Any]) -> List[str]:
    """Return the Codex runner fields exported for one profile."""
    return [
        _json_output("codex_environment_json", codex_environment),
        f"codex_auth_file={codex_environment.get('auth_file', '')}",
        _json_output(
            "codex_runner_labels_json", codex_environment.get("runner_labels", [])
        ),
    ]


def _coverage_output_lines(
    profile: Dict[str, Any],
    coverage_input_files: str,
    codecov_enabled: str,
    qlty_enabled: str,
) -> List[str]:
    """Return the coverage-related fields exported for one profile."""
    coverage = profile.get("coverage", {})
    issue_policy = profile.get("issue_policy", {})
    setup = coverage.get("setup", {})
    java = setup.get("java", {})
    return [
        _json_output("coverage_json", coverage),
        _json_output("issue_policy_json", issue_policy),
        f"coverage_runner={coverage.get('runner', 'ubuntu-latest')}",
        f"coverage_shell={coverage.get('shell', 'bash')}",
        f"coverage_node_version={setup.get('node', '')}",
        f"coverage_go_version={setup.get('go', '')}",
        f"coverage_dotnet_version={setup.get('dotnet', '')}",
        f"coverage_java_distribution={java.get('distribution', '')}",
        f"coverage_java_version={java.get('version', '')}",
        f"coverage_needs_rust={str(bool(setup.get('rust', False))).lower()}",
        _json_output("coverage_system_packages_json", setup.get("system_packages", [])),
        f"codecov_enabled={codecov_enabled}",
        f"coverage_input_files={coverage_input_files}",
        f"qlty_enabled={qlty_enabled}",
        f"qlty_coverage_files={coverage_input_files}",
    ]


def _profile_json_output(profile: Dict[str, Any]) -> List[str]:
    """Handle profile json output."""
    return [
        "profile_json<<__PROFILE__",
        json.dumps(profile, indent=2, sort_keys=True),
        "__PROFILE__",
    ]


def _github_output_lines(profile: Dict[str, Any], event_name: str) -> List[str]:
    """Handle github output lines."""
    return [*_profile_output_lines(profile, event_name), *_profile_json_output(profile)]


def _write_github_output(path: Path, profile: Dict[str, Any], event_name: str) -> None:
    """Handle write github output."""
    payload_lines = _github_output_lines(profile, event_name)
    with path.open("a", encoding="utf-8") as handle:
        for line in payload_lines:
            handle.write(line + "\n")


def main() -> int:
    """Handle main."""
    args = _parse_args()
    inventory = load_inventory(args.inventory) if args.inventory else load_inventory()
    profile = load_repo_profile(inventory, args.repo_slug)
    export_payload = dict(profile)
    export_payload["event_name"] = args.event_name
    export_payload["active_required_contexts"] = active_required_contexts(
        profile, event_name=args.event_name
    )

    output_target = args.out_json or args.output
    if output_target:
        output_path = Path(output_target)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(export_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        print(json.dumps(export_payload, indent=2, sort_keys=True))

    if args.github_output:
        _write_github_output(Path(args.github_output), profile, args.event_name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
