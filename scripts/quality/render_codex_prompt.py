#!/usr/bin/env python3
"""Render codex prompt."""

from __future__ import absolute_import

import argparse
from pathlib import Path
import sys
from typing import Iterable, List, cast

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.control_plane import active_required_contexts, load_inventory, load_repo_profile


def _parse_args() -> argparse.Namespace:
    """Handle parse args."""
    parser = argparse.ArgumentParser(description="Render Codex remediation/backlog prompts from a repo profile.")
    parser.add_argument("--inventory", default="")
    parser.add_argument("--repo-slug", required=True)
    parser.add_argument("--lane", choices=("remediation", "backlog"), default="remediation")
    parser.add_argument("--event-name", default="pull_request")
    parser.add_argument("--failure-context", default="")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--output", default="")
    return parser.parse_args()


def _artifact_lines(artifacts: List[object]) -> str:
    """Handle artifact lines."""
    return "\n".join(f"- {item}" for item in artifacts) or "- None"


def _repo_contract_lines(profile: dict) -> List[str]:
    """Handle repo contract lines."""
    codex_environment = profile.get("codex_environment", {})
    return [
        "## Repo contract",
        "",
        f"- Verify command: `{profile['verify_command']}`",
        f"- GitHub mutation lane: `{profile.get('github_mutation_lane', 'codex-private-runner')}`",
        f"- Codex auth lane: `{profile.get('codex_auth_lane', 'chatgpt-account')}`",
        f"- Provider UI mode: `{profile.get('provider_ui_mode', 'playwright-manual-login')}`",
        f"- Codex environment mode: `{codex_environment.get('mode', 'automatic')}`",
        f"- Codex environment verify command: `{codex_environment.get('verify_command', profile['verify_command'])}`",
        f"- Codex auth file: `{codex_environment.get('auth_file', '~/.codex/auth.json')}`",
        f"- Codex environment network profile: `{codex_environment.get('network_profile', 'unrestricted')}`",
        f"- Codex environment methods: `{codex_environment.get('methods', 'all')}`",
        f"- Codex runner labels: `{', '.join(codex_environment.get('runner_labels', []))}`",
        f"- Default branch: `{profile['default_branch']}`",
        f"- Preserve public check names: `{profile['preserve_public_check_names']}`",
    ]


def _required_contexts_lines(profile: dict, *, event_name: str) -> List[str]:
    """Handle required contexts lines."""
    contexts = active_required_contexts(profile, event_name=event_name)
    return [
        f"## Required contexts for {event_name}",
        "",
        *[f"- `{name}`" for name in contexts],
    ]


def _render_prompt(*args: object, **kwargs: object) -> str:
    """Handle render prompt."""
    if len(args) != 1:
        raise TypeError("_render_prompt expects a single profile mapping positional argument")
    profile = args[0]
    if not isinstance(profile, dict):
        raise TypeError("_render_prompt expects profile to be a mapping")
    try:
        lane = str(kwargs.pop("lane"))
        event_name = str(kwargs.pop("event_name"))
        failure_context = str(kwargs.pop("failure_context"))
        artifacts_value = kwargs.pop("artifacts")
    except KeyError as exc:  # pragma: no cover - defensive contract guard
        raise TypeError(f"Missing required prompt field: {exc.args[0]}") from exc
    if not isinstance(artifacts_value, Iterable):
        raise TypeError("_render_prompt expects artifacts to be iterable")
    artifacts = list(cast(Iterable[object], artifacts_value))
    if kwargs:
        raise TypeError(f"Unexpected _render_prompt parameters: {', '.join(sorted(kwargs))}")
    headline = "PR failure remediation" if lane == "remediation" else "backlog sweep"
    sections = [
        f"# Codex {headline}",
        "",
        f"Repo: {profile['slug']}",
        f"Lane: {lane}",
        f"Failure context: {failure_context or 'n/a'}",
        "",
        "Treat missing external statuses as policy drift, provider drift, or secret drift before changing code.",
        "Never push to the default branch. Use `codex/fix/<context>/<shortsha>` for remediation and `codex/backlog/<tool>` for backlog work.",
        "",
        *_repo_contract_lines(profile),
        "",
        *_required_contexts_lines(profile, event_name=event_name),
        "",
        "## Artifacts",
        "",
        _artifact_lines(artifacts),
    ]
    return "\n".join(sections) + "\n"


def main() -> int:
    """Handle main."""
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
