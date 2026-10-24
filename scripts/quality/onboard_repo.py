#!/usr/bin/env python3
"""One-command fleet onboarding: measure, generate, validate, print next steps.

Brings a repo from "not governed" to "lean member (rollout: observe)" in one
run, modeled on the fractal-darkroom onboarding (PR #312):

1. **detect the stack** from file signatures (``pyproject.toml``/``setup.py``
   -> ``python-tooling``; ``package.json`` -> ``node-frontend``; both ->
   ``fullstack-web``; ``go.mod`` -> ``go``; CMake/C++ -> ``cpp-cmake``;
   ``*.sln``/``*.csproj`` -> ``dotnet-wpf``; ``build.gradle*`` ->
   ``gradle-java``; a Python manifest naming flask/django/fastapi ->
   ``python-web``). The signature set mirrors the language detection in
   ``reusable-quality.yml`` plus the stack names in ``profiles/stacks/``.
2. **measure emitted contexts** on the default branch via
   ``gh api repos/<slug>/commits/<branch>/check-runs`` (the same call
   ``validate_required_contexts.py`` uses).
3. **generate** the inventory entry + a schema-v2 profile skeleton
   (``mode.phase: shadow``, required set = the measured contexts) modeled on
   ``profiles/repos/fractal-darkroom.yml``.
4. **run the phantom-context validator** over the generated skeleton.
5. **print the exact next commands** (copy files, regenerate rulesets, PR,
   re-measure on a PR head before flipping rollout off observe).

``--dry-run`` (the default posture for smoke tests) writes everything into a
temp directory and prints it; nothing in the repo is touched. Without
``--dry-run`` the profile is written to ``profiles/repos/`` and the inventory
entry is appended to ``inventory/repos.yml`` — but nothing is ever committed
by this tool.

Run it from the platform repo root as a module (it imports
``scripts.quality.validate_required_contexts``)::

    python -m scripts.quality.onboard_repo --slug Prekzursil/<name> --dry-run
"""

from __future__ import absolute_import

import argparse
import shutil
import subprocess  # nosec B404 — gh CLI wrapper; all args are controlled
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

from scripts.quality.validate_required_contexts import (
    build_report,
    fetch_measured_contexts,
    render_text,
)

# Same runner indirection fleet_inventory.py uses (test-injectable, and it
# keeps the S603/S607 bandit rules away from the call site).
ProcessRunner = Callable[..., "subprocess.CompletedProcess[str]"]

# Framework markers -> python-web (checked inside Python manifests).
_PY_WEB_MARKERS = ("flask", "django", "fastapi")
# Desktop/GUI markers -> python-desktop.
_PY_DESKTOP_MARKERS = ("pyside", "pyqt", "tkinter")


def detect_stack(source_dir: Path) -> Tuple[str, str]:
    """Return ``(stack, rationale)`` from the repo's file signatures."""
    files = {entry.name.lower() for entry in source_dir.iterdir()}
    py_manifest = bool(files & {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "requirements-dev.txt"})
    has_package_json = "package.json" in files
    if "go.mod" in files:
        return "go", "go.mod in the repo root"
    if "cmakelists.txt" in files or any(f.endswith((".cpp", ".cc", ".cxx")) for f in files):
        return "cpp-cmake", "CMakeLists.txt or C++ sources in the repo root"
    if any(f.endswith((".sln", ".csproj")) for f in files):
        return "dotnet-wpf", ".sln/.csproj in the repo root"
    if any(f in ("build.gradle", "build.gradle.kts") for f in files):
        return "gradle-java", "build.gradle in the repo root"
    if has_package_json and py_manifest:
        return "fullstack-web", "package.json plus a Python manifest in the repo root"
    if has_package_json:
        return "node-frontend", "package.json in the repo root"
    if py_manifest:
        manifest_text = " ".join(
            (source_dir / name).read_text(encoding="utf-8", errors="replace").lower()
            for name in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "requirements-dev.txt")
            if (source_dir / name).is_file()
        )
        if any(marker in manifest_text for marker in _PY_WEB_MARKERS):
            return "python-web", "Python manifest names a web framework (flask/django/fastapi)"
        if any(marker in manifest_text for marker in _PY_DESKTOP_MARKERS):
            return "python-desktop", "Python manifest names a desktop/GUI toolkit (pyside/pyqt/tkinter)"
        return "python-tooling", "Python manifest in the repo root, no web/desktop markers"
    return "", "no known stack signature in the repo root"


def profile_name_for_slug(slug: str) -> str:
    """Return the conventional profile name for a slug (repo name, lowercased)."""
    return slug.split("/", 1)[-1].lower()


def derive_codeql_languages(measured: Sequence[str]) -> List[str]:
    """Derive ``codeql.languages`` from measured ``Analyze (<lang>)`` names."""
    languages: List[str] = []
    for name in measured:
        if name.startswith("Analyze (") and name.endswith(")"):
            language = name[len("Analyze (") : -1]
            if language and language not in languages:
                languages.append(language)
    return languages


def derive_required_contexts(measured: Sequence[str]) -> Tuple[List[str], str]:
    """Build the skeleton's required set from the measured context names.

    Every measured name becomes required (the skeleton is a MEASURED baseline
    a human curates before enforcing); when CodeQL default setup is detected
    (``Analyze (<lang>)`` on main), the aggregate PR-only ``CodeQL`` context is
    added per fleet convention (fractal-darkroom / DevExtreme precedent).
    """
    required = [name for name in measured if not name.startswith("Analyze (")]
    note = "required set = every context measured on the default branch HEAD"
    if any(name.startswith("Analyze (") for name in measured):
        required.append("CodeQL")
        note += (
            "; aggregate CodeQL context added (PR-only - verify on a PR head,"
            " see validate_required_contexts.py PR_ONLY_CONTEXTS)"
        )
    return required, note


def coverage_for_stack(stack: str, source_dir: Path) -> Dict[str, Any]:
    """Return the skeleton ``coverage`` mapping for the detected stack."""
    if stack in ("python-tooling", "python-web", "python-desktop", "fullstack-web"):
        pytest_cfg = any(
            (source_dir / name).is_file()
            for name in ("pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini", "conftest.py")
        )
        if pytest_cfg:
            command = "python -m pytest --cov --cov-report=xml:coverage.xml\n"
        else:
            command = "python -m coverage run -m unittest discover\n"
        return {
            "command": command,
            "inputs": [{"format": "xml", "name": "coverage", "path": "coverage.xml", "flag": "coverage"}],
        }
    if stack in ("node-frontend",):
        return {
            "command": "npm run test:coverage\n",
            "inputs": [{"format": "lcov", "name": "coverage", "path": "coverage/lcov.info", "flag": "coverage"}],
        }
    return {
        "command": "# TODO: measure coverage with the repo's own gate script\n",
        "inputs": [],
    }


def verify_command_for_stack(stack: str) -> str:
    """Return the skeleton ``verify_command`` for the detected stack."""
    if stack.startswith("python") or stack == "fullstack-web":
        return "python -m pytest"
    if stack in ("node-frontend",):
        return "npm test"
    if stack == "go":
        return "go test ./..."
    return "# TODO: set the repo's verify command"


def dependabot_for_stack(stack: str, source_dir: Path) -> List[Dict[str, str]]:
    """Return the skeleton ``dependabot.updates`` rows for the detected stack."""
    updates: List[Dict[str, str]] = []
    py = any((source_dir / name).is_file() for name in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"))
    if py or stack in ("python-tooling", "python-web", "python-desktop"):
        updates.append({"ecosystem": "pip", "directory": "/"})
    if (source_dir / "package.json").is_file() or stack in ("node-frontend", "fullstack-web"):
        updates.append({"ecosystem": "npm", "directory": "/"})
    if (source_dir / "go.mod").is_file() or stack == "go":
        updates.append({"ecosystem": "gomod", "directory": "/"})
    return updates


def render_profile(
    slug: str,
    stack: str,
    required: Sequence[str],
    codeql_languages: Sequence[str],
    coverage: Mapping[str, Any],
    verify_command: str,
    dependabot_updates: Sequence[Mapping[str, str]],
    measured_note: str,
    branch: str,
) -> str:
    """Render the schema-v2 profile skeleton YAML (modeled on fractal-darkroom.yml)."""
    lines = [
        f"slug: {slug}",
        "version: 2",
        f"stack: {stack}",
        "mode:",
        "  phase: shadow",
        "scanners: {}",
        "overrides: []",
        f"verify_command: {verify_command}",
        "required_contexts_mode: replace",
        "issue_policy:",
        "  mode: ratchet",
        "  pr_behavior: introduced_only",
        "  main_behavior: absolute",
        "# SKELETON generated by scripts/quality/onboard_repo.py (phase shadow).",
        f"# {measured_note} on {branch}.",
        "# CURATE before flipping rollout off observe: drop push-only jobs",
        "# (pages deploys, scheduled workflows, dependency-graph) - nothing that",
        "# cannot report on a pull_request may be required.",
        "required_contexts:",
        "  always:",
    ]
    lines.extend(f"    - {name}" for name in required)
    lines.append("  pull_request_only: []")
    lines.append("  target:")
    lines.extend(f"    - {name}" for name in required)
    lines.append("coverage:")
    lines.append("  command: |")
    lines.extend(f"    {line}" for line in str(coverage["command"]).rstrip("\n").splitlines())
    if coverage["inputs"]:
        lines.append("  inputs:")
        for entry in coverage["inputs"]:
            lines.append(f"    - format: {entry['format']}")
            lines.append(f"      name: {entry['name']}")
            lines.append(f"      path: {entry['path']}")
            lines.append(f"      flag: {entry['flag']}")
    lines.append("codeql:")
    lines.append("  languages:")
    lines.extend(f"    - {language}" for language in codeql_languages)
    if not codeql_languages:
        lines.append("    [] # TODO: set languages if CodeQL runs on this repo")
    lines.append("dependabot:")
    lines.append("  updates:")
    if dependabot_updates:
        for row in dependabot_updates:
            lines.append(f"    - ecosystem: {row['ecosystem']}")
            lines.append(f"      directory: {row['directory']}")
    else:
        lines.append("    [] # TODO: add ecosystems for the repo's manifests")
    return "\n".join(lines) + "\n"


def render_inventory_entry(slug: str, profile: str, branch: str, stack: str) -> str:
    """Render the inventory/repos.yml row for the onboarded repo."""
    return (
        f"  - slug: {slug}\n"
        f"    profile: {profile}\n"
        f"    rollout: observe\n"
        f"    default_branch: {branch}\n"
        f"    notes: Lean member onboarded by scripts/quality/onboard_repo.py"
        f" (stack {stack}, phase shadow). Curate required_contexts before enforcing.\n"
    )


def next_commands(
    *,
    profile_name: str,
    profile_path: Path,
    inventory_fragment: str,
    dry_run: bool,
    temp_dir: Path | None,
) -> List[str]:
    """Return the exact follow-up commands printed at the end of a run."""
    if dry_run and temp_dir is not None:
        return [
            "# 1. copy the generated profile into the platform:",
            f'cp "{temp_dir / "profiles" / "repos" / (profile_name + ".yml")}" profiles/repos/',
            "# 2. append this entry to inventory/repos.yml:",
            inventory_fragment,
            "# 3. regenerate the ruleset payload + update the fleet-size tripwire",
            "#    (tests/test_control_plane.py pins len(inventory['repos'])):",
            (
                "python scripts/quality/generate_ruleset_payload.py"
                " --inventory inventory/repos.yml --output-dir generated/rulesets"
            ),
            "# 4. run the platform verifier locally, then open a PR:",
            "bash scripts/verify",
            "# 5. after merge: re-measure required contexts on a PR head before",
            "#    flipping rollout off observe (PR-only contexts like the aggregate",
            "#    CodeQL only report on pull_request events).",
        ]
    return [
        f"# 1. profile written to {profile_path}",
        "# 2. inventory entry appended (see the fragment above)",
        (
            "python scripts/quality/generate_ruleset_payload.py"
            " --inventory inventory/repos.yml --output-dir generated/rulesets"
        ),
        "# 3. update the fleet-size tripwire in tests/test_control_plane.py, then:",
        "bash scripts/verify",
        "# 4. commit + PR; re-measure on a PR head before flipping rollout off observe.",
    ]


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", required=True, help="owner/name of the repo to onboard")
    parser.add_argument(
        "--source-dir",
        default="",
        help="local checkout used for stack detection (default: shallow-clone the slug into a temp dir)",
    )
    parser.add_argument("--branch", default="", help="default branch (default: the repo's GitHub default)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="write nothing into this repo; emit the skeleton + validator output only",
    )
    return parser


def _resolve_default_branch(slug: str, *, runner: ProcessRunner) -> str:
    """Fetch the repo's default branch name via ``gh api``."""
    completed = runner(
        ["gh", "api", f"repos/{slug}", "--jq", ".default_branch"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SystemExit(f"cannot read repo metadata for {slug}: {(completed.stderr or 'gh failed').strip()}")
    branch = (completed.stdout or "").strip()
    if not branch:
        raise SystemExit(f"repo metadata for {slug} carried no default_branch")
    return branch


def _shallow_clone(slug: str, dest: Path, *, runner: ProcessRunner) -> None:
    """Shallow-clone ``slug`` into ``dest`` for stack detection."""
    result = runner(  # nosec S603 — argv array, controlled inputs
        ["gh", "repo", "clone", slug, str(dest), "--", "--depth", "1"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"cannot clone {slug}: {(result.stderr or 'clone failed').strip()}")


def main(
    argv: List[str] | None = None,
    *,
    root: Path | None = None,
    runner: ProcessRunner = subprocess.run,
) -> int:
    """Run the onboarding CLI."""
    args = _build_parser().parse_args(argv)
    platform_root = root if root is not None else Path(__file__).resolve().parents[2]
    branch = args.branch or _resolve_default_branch(args.slug, runner=runner)

    clone_dir: Path | None = None
    source_dir = Path(args.source_dir) if args.source_dir else None
    if source_dir is None:
        clone_dir = Path(tempfile.mkdtemp(prefix="qzp-onboard-")) / "repo"
        _shallow_clone(args.slug, clone_dir, runner=runner)
        source_dir = clone_dir
    try:
        stack, rationale = detect_stack(source_dir)
        if not stack:
            raise SystemExit(f"cannot detect a stack for {args.slug}: {rationale}")
        print(f"stack: {stack} ({rationale})")

        measured, measure_error = fetch_measured_contexts(args.slug, branch, runner=runner)
        if measured is None:
            raise SystemExit(f"cannot measure contexts for {args.slug}@{branch}: {measure_error}")
        print(f"measured {len(measured)} context(s) on {args.slug}@{branch}:")
        for name in measured:
            print(f"  - {name}")

        required, note = derive_required_contexts(measured)
        codeql_languages = derive_codeql_languages(measured)
        coverage = coverage_for_stack(stack, source_dir)
        verify_command = verify_command_for_stack(stack)
        dependabot_updates = dependabot_for_stack(stack, source_dir)
        profile_name = profile_name_for_slug(args.slug)
        profile_yaml = render_profile(
            args.slug,
            stack,
            required,
            codeql_languages,
            coverage,
            verify_command,
            dependabot_updates,
            note,
            branch,
        )
        inventory_fragment = render_inventory_entry(args.slug, profile_name, branch, stack)

        if args.dry_run:
            out_dir = Path(tempfile.mkdtemp(prefix="qzp-onboard-out-"))
            profile_dir = out_dir / "profiles" / "repos"
            profile_dir.mkdir(parents=True)
            profile_path = profile_dir / f"{profile_name}.yml"
            profile_path.write_text(profile_yaml, encoding="utf-8", newline="\n")
            inv_path = out_dir / "inventory"
            inv_path.mkdir()
            (inv_path / "repos.yml").write_text(
                f"version: 1\nrepos:\n{inventory_fragment}", encoding="utf-8", newline="\n"
            )
            print(f"\ndry-run: skeleton written under {out_dir} (nothing in this repo was touched)")
            print(f"--- {profile_path} ---")
            print(profile_yaml, end="")
            print("--- inventory entry ---")
            print(inventory_fragment, end="")
            report = build_report(
                [{"slug": args.slug, "profile": profile_name, "default_branch": branch}],
                profile_dir,
                runner=runner,
            )
            print("--- phantom-context validator on the generated skeleton ---")
            print(render_text(report))
            commands = next_commands(
                profile_name=profile_name,
                profile_path=profile_path,
                inventory_fragment=inventory_fragment,
                dry_run=True,
                temp_dir=out_dir,
            )
        else:
            profile_path = platform_root / "profiles" / "repos" / f"{profile_name}.yml"
            if profile_path.exists():
                raise SystemExit(f"refusing to overwrite existing profile {profile_path}")
            profile_path.write_text(profile_yaml, encoding="utf-8", newline="\n")
            inventory_path = platform_root / "inventory" / "repos.yml"
            text = inventory_path.read_text(encoding="utf-8")
            if not text.endswith("\n"):
                text += "\n"
            inventory_path.write_text(text + inventory_fragment, encoding="utf-8", newline="\n")
            print(f"profile written: {profile_path}")
            print(f"inventory entry appended: {inventory_path}")
            report = build_report(
                [{"slug": args.slug, "profile": profile_name, "default_branch": branch}],
                profile_path.parent,
                runner=runner,
            )
            print("--- phantom-context validator on the generated skeleton ---")
            print(render_text(report))
            commands = next_commands(
                profile_name=profile_name,
                profile_path=profile_path,
                inventory_fragment=inventory_fragment,
                dry_run=False,
                temp_dir=None,
            )
        print("--- next commands ---")
        for line in commands:
            print(line)
        return 0
    finally:
        if clone_dir is not None:
            shutil.rmtree(clone_dir.parent, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
