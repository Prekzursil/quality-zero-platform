#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.control_plane import active_required_contexts, load_inventory, load_repo_profile


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a resolved control-plane profile for workflows.")
    parser.add_argument("--inventory", default="")
    parser.add_argument("--repo-slug", required=True)
    parser.add_argument("--event-name", default="pull_request")
    parser.add_argument("--output", default="")
    parser.add_argument("--out-json", default="")
    parser.add_argument("--github-output", default="")
    return parser.parse_args()


def _write_github_output(path: Path, profile: dict, event_name: str) -> None:
    contexts = active_required_contexts(profile, event_name=event_name)
    coverage = profile.get("coverage", {})
    codex_environment = profile.get("codex_environment", {})
    setup = coverage.get("setup", {})
    java = setup.get("java", {})
    qlty_files = ",".join(item["path"] for item in coverage.get("inputs", []))
    payload_lines = [
        f"verify_command={profile['verify_command']}",
        f"default_branch={profile['default_branch']}",
        f"profile_id={profile['profile_id']}",
        f"stack={profile['stack']}",
        f"required_contexts_json={json.dumps(contexts, separators=(',', ':'))}",
        f"required_contexts_required_now_json={json.dumps(profile['required_contexts']['required_now'], separators=(',', ':'))}",
        f"required_secrets_json={json.dumps(profile['required_secrets'], separators=(',', ':'))}",
        f"required_vars_json={json.dumps(profile['required_vars'], separators=(',', ':'))}",
        f"codex_environment_json={json.dumps(codex_environment, separators=(',', ':'))}",
        f"coverage_json={json.dumps(coverage, separators=(',', ':'))}",
        f"coverage_runner={coverage.get('runner', 'ubuntu-latest')}",
        f"coverage_shell={coverage.get('shell', 'bash')}",
        f"coverage_node_version={setup.get('node', '')}",
        f"coverage_go_version={setup.get('go', '')}",
        f"coverage_dotnet_version={setup.get('dotnet', '')}",
        f"coverage_java_distribution={java.get('distribution', '')}",
        f"coverage_java_version={java.get('version', '')}",
        f"coverage_needs_rust={str(bool(setup.get('rust', False))).lower()}",
        f"coverage_system_packages_json={json.dumps(setup.get('system_packages', []), separators=(',', ':'))}",
        f"qlty_enabled={str(bool(profile.get('enabled_scanners', {}).get('qlty', False))).lower()}",
        f"qlty_coverage_files={qlty_files}",
        f"enabled_scanners_json={json.dumps(profile.get('enabled_scanners', {}), separators=(',', ':'))}",
        f"vendors_json={json.dumps(profile.get('vendors', {}), separators=(',', ':'))}",
    ]
    with path.open("a", encoding="utf-8") as handle:
        for line in payload_lines:
            handle.write(line + "\n")
        handle.write("profile_json<<__PROFILE__\n")
        handle.write(json.dumps(profile, indent=2, sort_keys=True) + "\n")
        handle.write("__PROFILE__\n")


def main() -> int:
    args = _parse_args()
    inventory = load_inventory(args.inventory) if args.inventory else load_inventory()
    profile = load_repo_profile(inventory, args.repo_slug)
    export_payload = dict(profile)
    export_payload["event_name"] = args.event_name
    export_payload["active_required_contexts"] = active_required_contexts(profile, event_name=args.event_name)

    output_target = args.out_json or args.output
    if output_target:
        output_path = Path(output_target)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(export_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        print(json.dumps(export_payload, indent=2, sort_keys=True))

    if args.github_output:
        _write_github_output(Path(args.github_output), profile, args.event_name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
