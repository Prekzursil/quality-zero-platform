#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.control_plane import active_required_contexts, load_inventory, load_repo_profile


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render Codex remediation/backlog prompts from a repo profile.")
    parser.add_argument("--inventory", default="")
    parser.add_argument("--repo-slug", required=True)
    parser.add_argument("--lane", choices=("remediation", "backlog"), default="remediation")
    parser.add_argument("--event-name", default="pull_request")
    parser.add_argument("--failure-context", default="")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--output", default="")
    return parser.parse_args()


def _render_prompt(profile: dict, *, lane: str, event_name: str, failure_context: str, artifacts: list[str]) -> str:
    contexts = active_required_contexts(profile, event_name=event_name)
    artifact_lines = "\n".join(f"- {item}" for item in artifacts) or "- None"
    headline = "PR failure remediation" if lane == "remediation" else "backlog sweep"
    codex_environment = profile.get("codex_environment", {})
    return f"""# Codex {headline}

Repo: {profile['slug']}
Lane: {lane}
Failure context: {failure_context or 'n/a'}

Treat missing external statuses as policy drift, provider drift, or secret drift before changing code.
Never push to the default branch. Use `codex/fix/<context>/<shortsha>` for remediation and `codex/backlog/<tool>` for backlog work.

## Repo contract

- Verify command: `{profile['verify_command']}`
- GitHub mutation lane: `{profile.get('github_mutation_lane', 'codex-private-runner')}`
- Codex auth lane: `{profile.get('codex_auth_lane', 'chatgpt-account')}`
- Provider UI mode: `{profile.get('provider_ui_mode', 'playwright-manual-login')}`
- Codex environment mode: `{codex_environment.get('mode', 'automatic')}`
- Codex environment verify command: `{codex_environment.get('verify_command', profile['verify_command'])}`
- Codex auth file: `{codex_environment.get('auth_file', '~/.codex/auth.json')}`
- Codex environment network profile: `{codex_environment.get('network_profile', 'unrestricted')}`
- Codex environment methods: `{codex_environment.get('methods', 'all')}`
- Codex runner labels: `{", ".join(codex_environment.get('runner_labels', []))}`
- Default branch: `{profile['default_branch']}`
- Preserve public check names: `{profile['preserve_public_check_names']}`

## Required contexts for {event_name}

{chr(10).join(f'- `{name}`' for name in contexts)}

## Artifacts

{artifact_lines}
"""


def main() -> int:
    args = _parse_args()
    inventory = load_inventory(args.inventory) if args.inventory else load_inventory()
    profile = load_repo_profile(inventory, args.repo_slug)
    prompt = _render_prompt(
        profile,
        lane=args.lane,
        event_name=args.event_name,
        failure_context=args.failure_context,
        artifacts=args.artifact,
    )
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(prompt, encoding="utf-8")
    else:
        print(prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
