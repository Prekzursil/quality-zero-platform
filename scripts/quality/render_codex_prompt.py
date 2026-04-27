#!/usr/bin/env python3
"""Render codex prompt."""

from __future__ import absolute_import

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, cast

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.control_plane import (
    active_required_contexts,
    load_inventory,
    load_repo_profile,
)
from scripts.quality.known_issues import load_known_issues, qrv2_prompt_entries

_KNOWN_ISSUES_ROOT = Path(__file__).resolve().parents[2] / "known-issues"


def _parse_args() -> argparse.Namespace:
    """Handle parse args."""
    parser = argparse.ArgumentParser(description="Render Codex remediation/backlog prompts from a repo profile.")
    parser.add_argument("--inventory", default="")
    parser.add_argument("--repo-slug", required=True)
    parser.add_argument("--lane", choices=("remediation", "backlog"), default="remediation")
    parser.add_argument("--event-name", default="pull_request")
    parser.add_argument("--failure-context", default="")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--canonical-json", default="")
    parser.add_argument("--output", default="")
    return parser.parse_args()


def _artifact_lines(artifacts: List[object]) -> str:
    """Handle artifact lines."""
    return "\n".join(f"- {item}" for item in artifacts) or "- None"


def _repo_contract_lines(profile: dict) -> List[str]:
    """Handle repo contract lines."""
    codex_environment = profile.get("codex_environment", {})
    codex_auth_file = codex_environment.get("auth_file", "~/.codex/auth.json")
    runner_labels = ", ".join(codex_environment.get("runner_labels", []))
    github_mutation_lane = profile.get("github_mutation_lane", "codex-private-runner")
    provider_ui_mode = profile.get("provider_ui_mode", "playwright-manual-login")
    return [
        "## Repo contract",
        "",
        f"- Verify command: `{profile['verify_command']}`",
        f"- GitHub mutation lane: `{github_mutation_lane}`",
        f"- Codex auth lane: `{profile.get('codex_auth_lane', 'chatgpt-account')}`",
        f"- Provider UI mode: `{provider_ui_mode}`",
        f"- Codex environment mode: `{codex_environment.get('mode', 'automatic')}`",
        (f"- Codex environment verify command: `{codex_environment.get('verify_command', profile['verify_command'])}`"),
        f"- Codex auth file: `{codex_auth_file}`",
        (f"- Codex environment network profile: `{codex_environment.get('network_profile', 'unrestricted')}`"),
        f"- Codex environment methods: `{codex_environment.get('methods', 'all')}`",
        f"- Codex runner labels: `{runner_labels}`",
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


def _render_known_issues_section(
    registry_path: Path = _KNOWN_ISSUES_ROOT,
) -> str:
    """Render the known-issues registry as a Codex prompt section.

    QRv2 reads this section so the remediation loop applies the
    canonical fix for every recurring false positive instead of
    re-deriving it per run. Only entries with ``feeds_qrv2: true`` +
    a non-empty ``fix_snippet`` appear; the ``qrv2_prompt_entries``
    filter enforces that.

    Returns an empty string when the registry is empty — the caller
    simply omits the section in that case so older profiles don't see
    a dangling heading.
    """
    entries = qrv2_prompt_entries(load_known_issues(registry_path))
    if not entries:
        return ""
    lines: List[str] = [
        "## Known-issues registry",
        "",
        (
            "The entries below are documented false positives / recurring "
            "patterns with verified canonical fixes. When a CI failure "
            "matches one of them, apply the fix_snippet verbatim rather "
            "than inventing a new fix."
        ),
        "",
    ]
    for entry in entries:
        lines.append(f"### {entry.get('id', '?')} — {entry.get('title', '')}")
        lines.append("")
        description = str(entry.get("description", "")).strip()
        if description:
            lines.append(description)
            lines.append("")
        affects = entry.get("affects") or []
        if affects:
            lines.append(f"Affects: {', '.join(str(a) for a in affects)}")
            lines.append("")
        fix_snippet = str(entry.get("fix_snippet", "")).rstrip()
        if fix_snippet:
            lines.append("Canonical fix:")
            lines.append("")
            lines.append("```")
            lines.append(fix_snippet)
            lines.append("```")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _format_providers_line(corroborators: List[Dict[str, Any]]) -> str:
    """Render the corroborator provider list as a single Markdown bullet value."""
    provider_names = [c.get("provider", "") for c in corroborators if c.get("provider")]
    provider_str = ", ".join(provider_names) if provider_names else "1 provider"
    if len(provider_names) > 1:
        return f"{provider_str} ({len(provider_names)} agree)"
    return provider_str


def _render_finding_block(finding: Dict[str, Any], *, file_path: str) -> List[str]:
    """Render one finding's Markdown bullets — heading, severity, message, etc."""
    category = finding.get("category", "unknown")
    line_num = finding.get("line", "?")
    severity = finding.get("severity", "unknown")
    message = finding.get("primary_message", "")
    fix_hint = finding.get("fix_hint")
    patch_diff = finding.get("patch")
    corroborators = finding.get("corroborators", [])

    block: List[str] = [
        f"#### Finding: {category} (line {line_num} in {file_path})",
        f"- **Severity**: {severity}",
        f"- **Message**: {message}",
    ]
    if fix_hint:
        block.append(f"- **Fix hint**: {fix_hint}")
    block.append(f"- **Providers**: {_format_providers_line(corroborators)}")
    if patch_diff:
        block.extend(
            [
                "- **Suggested patch**:",
                "```diff",
                patch_diff.rstrip(),
                "```",
            ]
        )
    block.append("")
    return block


def _render_canonical_findings_section(findings: List[Dict[str, Any]]) -> str:
    """Render structured canonical findings for the remediation prompt.

    Groups findings by file, then delegates each per-finding render to
    ``_render_finding_block``. Returns an empty string if there are no
    findings. Previously ~50 lines with cyclomatic complexity 20; the
    extraction drops the orchestrator to a flat group-and-loop shape.
    """
    if not findings:
        return ""

    by_file: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for f in findings:
        by_file[f.get("file", "<unknown>")].append(f)

    lines: List[str] = ["## Findings requiring attention", ""]
    for file_path in sorted(by_file):
        lines.append(f"### File: {file_path}")
        lines.append("")
        for finding in by_file[file_path]:
            lines.extend(_render_finding_block(finding, file_path=file_path))

    return "\n".join(lines)


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
    artifacts = list(cast("Iterable[object]", artifacts_value))
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
        ("Treat missing external statuses as policy drift, provider drift, or secret drift before changing code."),
        (
            "Never push to the default branch. Use "
            "`codex/fix/<context>/<shortsha>` for remediation and "
            "`codex/backlog/<tool>` for backlog work."
        ),
        "",
        *_repo_contract_lines(profile),
        "",
        *_required_contexts_lines(profile, event_name=event_name),
        "",
        "## Artifacts",
        "",
        _artifact_lines(artifacts),
    ]
    rendered = "\n".join(sections) + "\n"
    known_issues_section = _render_known_issues_section()
    if known_issues_section:
        rendered = rendered.rstrip("\n") + "\n\n" + known_issues_section
    return rendered


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
    # Append structured canonical findings when provided
    if args.canonical_json:
        canonical_path = Path(args.canonical_json)
        if canonical_path.is_file():
            canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
            findings_section = _render_canonical_findings_section(canonical.get("findings", []))
            if findings_section:
                prompt = prompt.rstrip("\n") + "\n\n" + findings_section
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(prompt, encoding="utf-8")
    else:
        print(prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
