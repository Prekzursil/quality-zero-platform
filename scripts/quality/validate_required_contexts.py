#!/usr/bin/env python3
"""Phantom-context validator: profile required_contexts vs MEASURED check-runs.

A "phantom context" is a status-check name a profile REQUIRES but the repo
never emits on its default branch (measured via
``gh api repos/<slug>/commits/<branch>/check-runs``). Requiring one keeps a
branch permanently red — the exact failure mode that motivated this tool (see
the tracelines note in ``inventory/repos.yml``: the profile required
``codeql / CodeQL``, the repo runs CodeQL via DEFAULT setup, and that name is
never emitted on main).

PR-ONLY contexts are a measured, legitimate exception: the aggregate
``CodeQL`` check-run reports on ``pull_request`` events but not reliably on
push-to-main (fleet convention — see profiles/repos/fractal-darkroom.yml).
Those are marked as needing a PR-head measurement rather than counted as
phantoms.

Exit codes: ``0`` no phantom contexts; ``1`` at least one phantom (or an
unmeasurable repo — never reported clean); ``2`` the roster/profiles could
not be read at all.
"""

from __future__ import absolute_import

import argparse
import json
import subprocess  # nosec B404 — gh CLI wrapper; all args are controlled
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

import yaml  # type: ignore[import-untyped]

# A process runner callable compatible with ``subprocess.run``. Tests inject a
# fake so no real ``gh`` binary is needed (same indirection fleet_inventory.py
# uses, which also keeps the S603/S607 bandit rules out of the call site).
ProcessRunner = Callable[..., "subprocess.CompletedProcess[str]"]

# Contexts that only report on pull_request events. The aggregate CodeQL
# check-run is the known member: CodeQL DEFAULT setup emits per-language
# ``Analyze (<lang>)`` on push but the aggregate ``CodeQL`` name only on
# pull_request (16/16 WebCoder PR heads vs 1/16 for Analyze (<lang>), per the
# tracelines inventory note). Anything here is reported as
# ``pr_only_unverified_on_main`` instead of a phantom.
PR_ONLY_CONTEXTS = frozenset({"CodeQL"})

EXIT_OK = 0
EXIT_PHANTOM = 1
EXIT_ROSTER = 2


@dataclass(frozen=True)
class RepoVerdict:
    """The validator's result for one inventory entry."""

    slug: str
    branch: str
    required: Tuple[str, ...] = ()
    measured: Tuple[str, ...] = ()
    matched: Tuple[str, ...] = ()
    phantoms: Tuple[str, ...] = ()
    pr_only_unverified: Tuple[str, ...] = ()
    error: str = ""

    @property
    def status(self) -> str:
        """One of ``clean`` / ``phantom`` / ``pr_only`` / ``error``."""
        if self.error:
            return "error"
        if self.phantoms:
            return "phantom"
        if self.pr_only_unverified:
            return "pr_only"
        return "clean"


@dataclass(frozen=True)
class FleetReport:
    """Aggregated verdicts plus the roster-level problems, if any."""

    verdicts: Tuple[RepoVerdict, ...] = field(default_factory=tuple)
    roster_errors: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def status(self) -> str:
        """Overall status: ``roster_error`` / ``phantom`` / ``pr_only`` / ``clean``."""
        if self.roster_errors:
            return "roster_error"
        if any(v.phantoms or v.error for v in self.verdicts):
            return "phantom"
        if any(v.pr_only_unverified for v in self.verdicts):
            return "pr_only"
        return "clean"

    def exit_code(self) -> int:
        """Map the overall status onto the documented exit codes."""
        if self.roster_errors:
            return EXIT_ROSTER
        if self.status == "phantom":
            return EXIT_PHANTOM
        return EXIT_OK


def load_inventory_entries(inventory_path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    """Return inventory rows ``[{slug, profile, default_branch}]`` plus errors."""
    errors: List[str] = []
    payload = yaml.safe_load(inventory_path.read_text(encoding="utf-8")) or {}
    repos = payload.get("repos") if isinstance(payload, Mapping) else None
    if not isinstance(repos, list):
        return [], [f"{inventory_path}: no `repos` list could be read"]
    entries: List[Dict[str, str]] = []
    for item in repos:
        if not isinstance(item, Mapping):
            errors.append(f"{inventory_path}: non-mapping repos row skipped")
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            errors.append(f"{inventory_path}: repos row without a slug skipped")
            continue
        entries.append(
            {
                "slug": slug.strip(),
                "profile": str(item.get("profile") or ""),
                "default_branch": str(item.get("default_branch") or "main"),
            }
        )
    return entries, errors


def load_profile(profiles_dir: Path, profile_name: str) -> Tuple[Mapping[str, Any] | None, str]:
    """Load ``profiles/repos/<name>.yml``; return the mapping or an error message."""
    if not profile_name:
        return None, "inventory row has no `profile` name"
    path = profiles_dir / f"{profile_name}.yml"
    if not path.is_file():
        return None, f"profile file not found: {path}"
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, Mapping):
        return None, f"profile is not a mapping: {path}"
    return payload, ""


def collect_required(profile: Mapping[str, Any]) -> Tuple[List[str], str]:
    """Return the profile's required context list (target, else always) or an error."""
    section = profile.get("required_contexts")
    if not isinstance(section, Mapping):
        return [], "profile has no `required_contexts` mapping"
    chosen = section.get("target")
    if not isinstance(chosen, list) or not chosen:
        chosen = section.get("always")
    if not isinstance(chosen, list):
        return [], "`required_contexts.target`/`always` is not a list"
    names = [str(name).strip() for name in chosen if str(name).strip()]
    if not names:
        return [], "`required_contexts` target/always is empty"
    return names, ""


def resolve_context(context: str, measured: Sequence[str]) -> bool:
    """Resolve one required context against measured names.

    Mirrors :func:`scripts.quality.check_required_checks._resolve_observed_context`:
    an exact name match wins; otherwise a UNIQUE ``<job> / <context>`` suffix
    match resolves (so a required ``quality`` matches a matrix-emitted
    ``quality / quality`` only when exactly one such name exists).
    """
    if context in measured:
        return True
    suffix = f" / {context}"
    matches = [name for name in measured if name.endswith(suffix)]
    return len(matches) == 1


def classify(required: Sequence[str], measured: Sequence[str]) -> Tuple[List[str], List[str], List[str]]:
    """Split required contexts into matched / phantoms / PR-only-unverified."""
    matched: List[str] = []
    phantoms: List[str] = []
    pr_only: List[str] = []
    for context in required:
        if resolve_context(context, measured):
            matched.append(context)
        elif context in PR_ONLY_CONTEXTS:
            pr_only.append(context)
        else:
            phantoms.append(context)
    return matched, phantoms, pr_only


def fetch_measured_contexts(
    slug: str,
    branch: str,
    *,
    runner: ProcessRunner,
) -> Tuple[List[str] | None, str]:
    """Fetch the check-run names emitted on ``slug@branch`` via ``gh api``.

    Returns ``(names, "")`` on success or ``(None, message)`` when the repo
    cannot be measured (private repo without token access, missing branch, gh
    failure) — an unmeasurable repo is never reported clean.
    """
    completed = runner(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "-H",
            "Accept: application/vnd.github+json",
            f"repos/{slug}/commits/{branch}/check-runs?per_page=100",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        hint = detail[-1] if detail else f"exit {completed.returncode}"
        return None, f"gh api failed: {hint}"
    try:
        payload = json.loads(completed.stdout) if completed.stdout else []
    except json.JSONDecodeError as exc:
        return None, f"gh api returned unparseable JSON: {exc}"
    # `--slurp` yields one array per page wrapped in an outer array on newer
    # gh, or a flat array of response objects on older ones; accept both
    # (same normalization fleet_inventory._flatten_paginated_json applies).
    pages: List[Any] = []
    for item in payload if isinstance(payload, list) else [payload]:
        if isinstance(item, list):
            pages.extend(entry for entry in item if isinstance(entry, Mapping))
        elif isinstance(item, Mapping):
            pages.append(item)
    names: List[str] = []
    for page in pages:
        for run in page.get("check_runs", []) or []:
            name = str(run.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
    return names, ""


def validate_repo(
    slug: str,
    branch: str,
    profile: Mapping[str, Any] | None,
    profile_error: str,
    required_error: str,
    required: Sequence[str],
    *,
    runner: ProcessRunner,
) -> RepoVerdict:
    """Run one repo's profile-vs-measured comparison to a verdict."""
    if profile is None or profile_error or required_error:
        why = profile_error or required_error
        return RepoVerdict(slug=slug, branch=branch, required=tuple(required), error=why)
    measured, measure_error = fetch_measured_contexts(slug, branch, runner=runner)
    if measured is None:
        return RepoVerdict(
            slug=slug,
            branch=branch,
            required=tuple(required),
            error=f"unmeasurable: {measure_error}",
        )
    matched, phantoms, pr_only = classify(required, measured)
    return RepoVerdict(
        slug=slug,
        branch=branch,
        required=tuple(required),
        measured=tuple(measured),
        matched=tuple(matched),
        phantoms=tuple(phantoms),
        pr_only_unverified=tuple(pr_only),
    )


def build_report(
    entries: Sequence[Mapping[str, str]],
    profiles_dir: Path,
    *,
    runner: ProcessRunner,
) -> FleetReport:
    """Validate every inventory entry and aggregate to a fleet report.

    Per-profile problems (missing file, no ``required_contexts``) surface as
    that repo's verdict ``error`` (overall exit 1); ``roster_errors`` is
    reserved for inventory-level failures (exit 2).
    """
    verdicts: List[RepoVerdict] = []
    for entry in entries:
        slug = entry["slug"]
        branch = entry["default_branch"]
        profile, profile_error = load_profile(profiles_dir, entry["profile"])
        required: List[str] = []
        required_error = ""
        if profile is not None:
            required, required_error = collect_required(profile)
        verdicts.append(
            validate_repo(
                slug,
                branch,
                profile,
                profile_error,
                required_error,
                required,
                runner=runner,
            )
        )
    return FleetReport(verdicts=tuple(verdicts))


def report_json(report: FleetReport) -> Dict[str, Any]:
    """Render the fleet report as a JSON-serialisable mapping."""
    return {
        "status": report.status,
        "repos": [
            {
                "slug": v.slug,
                "branch": v.branch,
                "status": v.status,
                "required": list(v.required),
                "measured": list(v.measured),
                "matched": list(v.matched),
                "phantom_contexts": list(v.phantoms),
                "pr_only_unverified_on_main": list(v.pr_only_unverified),
                "error": v.error,
            }
            for v in report.verdicts
        ],
        "roster_errors": list(report.roster_errors),
    }


def render_text(report: FleetReport) -> str:
    """Render the fleet report as the human-readable CLI output."""
    lines = [f"phantom-context validator: {report.status}"]
    for v in report.verdicts:
        marker = {"clean": "ok", "phantom": "PHANTOM", "pr_only": "pr-only", "error": "ERROR"}[v.status]
        lines.append(f"  [{marker:>7}] {v.slug} (branch {v.branch})")
        if v.phantoms:
            lines.append(f"          required but NOT emitted on {v.branch}: {', '.join(v.phantoms)}")
        if v.pr_only_unverified:
            lines.append(
                f"          PR-only, needs a PR-head measurement (not on {v.branch}): {', '.join(v.pr_only_unverified)}"
            )
        if v.error:
            lines.append(f"          {v.error}")
    if report.roster_errors:
        lines.append("roster errors:")
        lines.extend(f"  - {item}" for item in report.roster_errors)
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inventory", default="inventory/repos.yml", help="fleet inventory YAML")
    parser.add_argument(
        "--profiles-dir",
        default=None,
        help="directory holding profiles/repos/<name>.yml (default: <repo root>/profiles/repos)",
    )
    parser.add_argument("--repo", default="", help="validate only this slug (owner/name)")
    parser.add_argument("--out-json", default="", help="also write the JSON report to this path")
    return parser


def main(
    argv: List[str] | None = None,
    *,
    runner: ProcessRunner = subprocess.run,
) -> int:
    """Run the phantom-context validator CLI."""
    args = _build_parser().parse_args(argv)
    inventory_path = Path(args.inventory)
    if not inventory_path.is_file():
        print(f"inventory not found: {inventory_path}", file=sys.stderr)
        return EXIT_ROSTER
    root = Path(__file__).resolve().parents[2]
    profiles_dir = Path(args.profiles_dir) if args.profiles_dir else root / "profiles" / "repos"
    entries, _errors = load_inventory_entries(inventory_path)
    if args.repo:
        entries = [entry for entry in entries if entry["slug"] == args.repo]
        if not entries:
            print(f"slug {args.repo!r} not found in {inventory_path}", file=sys.stderr)
            return EXIT_ROSTER
    report = build_report(entries, profiles_dir, runner=runner)
    if args.out_json:
        Path(args.out_json).write_text(json.dumps(report_json(report), indent=2) + "\n", encoding="utf-8")
    print(render_text(report))
    return report.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
