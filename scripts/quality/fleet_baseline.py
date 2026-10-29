#!/usr/bin/env python3
"""Fleet governance baseline: plan, render and audit what every governed repo carries.

quality-zero-platform is the fleet's governor. ``docs/FLEET-BASELINE.md`` is the
policy; this module is its executable half. For one repository checkout it
decides, file by file, what the baseline requires and what to do about it:

* **community files** — ``SECURITY.md`` (fractal-darkroom's shape: supported
  versions, a private reporting channel with a 72 h / 14 d SLA, scope, supply
  chain), ``CONTRIBUTING.md`` (the gates, the "never weaken a gate" rule, local
  verification, 100 % line+branch), ``CODE_OF_CONDUCT.md`` (Contributor
  Covenant 2.1), a pull-request template whose checklist maps 1:1 to the CI
  gates, issue forms that route security reports to the private channel, and a
  minimal ``AGENTS.md``;
* **Dependabot** — one update rail per manifest tree for every ecosystem the
  checkout actually contains, plus ``github-actions``. Security updates are
  grouped per ecosystem so ONE pull request can clear every open advisory
  (the dependency gate is zero tolerance, so a stranded advisory reddens every
  other PR); majors are never ignored, they arrive one by one;
* **the lean gate** — ``.github/workflows/quality.yml`` calling
  ``reusable-quality.yml`` pinned to an exact 40-hex commit (or a pin bump when
  a caller already exists), the gate-4/gate-6 configs the reusable workflow
  needs to run those gates at all, and ``scripts/verify``, which runs every
  lane locally at the pinned commit.

Ownership is conservative. A file the repository wrote itself is never
overwritten: missing files are created, files that are byte-identical to a
known platform-generated boilerplate (or carry the managed header) are
regenerated, and everything else is kept and — where it matters — audited and
reported as a gap. A thin lean-gate caller (one ``quality`` job that only calls
``reusable-quality.yml``) is platform-owned: it is regenerated from the
template with its inputs carried over. The rendered documents describe the
repository as it is: a checked-in CodeQL workflow, or a repository-authored
``scripts/verify``, pull-request template or Dependabot config, is described as
such. Nothing is committed or pushed here; ``--write`` only edits the checkout,
and it refuses to create a file that already exists on disk.

Usage, from the platform repo root::

    python -m scripts.quality.fleet_baseline --repo-dir ../consumer \\
        --slug Prekzursil/<name> --qzp-pin <40-hex sha> [--private] [--write]
"""

from __future__ import absolute_import

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import string
import subprocess  # nosec B404 — git wrapper; all args are controlled
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

import yaml  # type: ignore[import-untyped]

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # pragma: no cover

from scripts.quality.render_repo_baseline import render_security_policy as render_legacy_security_policy

ProcessRunner = Callable[..., "subprocess.CompletedProcess[str]"]
Reader = Callable[[str], Optional[str]]

PLATFORM_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_ROOT = PLATFORM_ROOT / "templates" / "fleet"
#: QZP's own curated gate-4 ruleset and gate-4 scope file: the single source the fleet is seeded from.
OPENGREP_SOURCE = PLATFORM_ROOT / ".quality" / "opengrep"
SEMGREPIGNORE_SOURCE = PLATFORM_ROOT / "docs" / "lean-gate" / "semgrepignore.template"
MAINTAINER_URL = "https://github.com/Prekzursil"
REUSABLE_QUALITY_RE = re.compile(
    r"(uses:\s*[\"']?Prekzursil/quality-zero-platform/\.github/workflows/reusable-quality\.yml@)([^\s#'\"]+)"
)
REUSABLE_QUALITY_PREFIX = "Prekzursil/quality-zero-platform/.github/workflows/reusable-quality.yml@"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DEPS_FLOORS = ("t0", "any")
#: An advanced CodeQL setup is a checked-in workflow that runs CodeQL itself or through QZP.
_CODEQL_INIT_RE = re.compile(r"github/codeql-action/init@")
_REUSABLE_CODEQL_RE = re.compile(r"Prekzursil/quality-zero-platform/\.github/workflows/reusable-codeql\.yml@")
_CODEQL_QUERIES_RE = re.compile(r"^[ \t]*queries:[ \t]*(.*)$", re.MULTILINE)
_CODEQL_CONFIG_RE = re.compile(r"^[ \t]*config(-file)?:", re.MULTILINE)
#: The managed scripts/verify carries this phrase; any other scripts/verify is the repository's own.
MANAGED_VERIFY_MARKER = "Managed by quality-zero-platform"
#: Top-level keys and caller-job keys a thin lean-gate caller may have; anything more is hand-written.
_THIN_CALLER_KEYS = frozenset({"name", "on", True, "permissions", "concurrency", "jobs"})
_THIN_CALLER_JOB_KEYS = frozenset({"uses", "with", "name", "permissions"})
_PLAIN_SCALAR_RE = re.compile(r"[A-Za-z0-9_./-]+")
#: The only token permissions a thin caller grants (the template sets them at the workflow level).
READ_ONLY = {"contents": "read"}

#: Path components holding third-party or installed code — never a rail. Everything else the
#: dependency gate scans (osv-scanner --recursive) gets a rail, build/ and fixtures/ included.
EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "vendor",
        "third_party",
        "__pycache__",
        "site-packages",
        ".tox",
    }
)
_PIP_NAMES = frozenset({"pyproject.toml", "setup.py", "setup.cfg", "Pipfile"})
_GRADLE_NAMES = frozenset({"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"})
_NUGET_SUFFIXES = (".csproj", ".fsproj", ".vbproj", ".sln")
_NUGET_NAMES = frozenset({"packages.config", "Directory.Packages.props"})
_COMPOSE_NAMES = frozenset({"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"})
_SIMPLE_MANIFESTS = {
    "package.json": "npm",
    "bun.lock": "bun",
    "bun.lockb": "bun",
    "uv.lock": "uv",
    "go.mod": "gomod",
    "Cargo.toml": "cargo",
    "composer.json": "composer",
    "Gemfile": "bundler",
    "Package.swift": "swift",
    "pubspec.yaml": "pub",
    "mix.exs": "mix",
    "pom.xml": "maven",
    "Chart.yaml": "helm",
}
#: Ecosystems whose root build can own nested modules: a nested manifest only loses its own
#: rail when the build file in an enclosing rail directory demonstrably declares it.
_ROOT_BUILD_FILES = {
    "gradle": ("settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts"),
    "maven": ("pom.xml",),
}
#: Ecosystems updated at the repository root regardless of where the marker file sits.
_ROOT_RAILS = frozenset({"gitsubmodule", "devcontainers"})
#: Ecosystems without semantic versions (update-types would be meaningless).
_NO_SEMVER = frozenset({"gitsubmodule"})
#: (winner, loser): a lockfile that selects a dedicated ecosystem for its directory.
_SUPERSEDES = (("bun", "npm"), ("uv", "pip"))

#: Extensions per language, aligned with the lean gate's own detection (reusable-quality.yml).
LANGUAGE_EXTENSIONS: Dict[str, Tuple[str, ...]] = {
    "python": (".py",),
    "ts": (".ts", ".tsx"),
    "js": (".js", ".jsx", ".mjs", ".cjs"),
    "go": (".go",),
    "cpp": (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"),
    "c": (".c",),
    "java": (".java",),
    "kotlin": (".kt",),
    "csharp": (".cs",),
    "rust": (".rs",),
    "swift": (".swift",),
}
#: Languages the lean gate does not measure; their repos must test them in their own CI.
_OUTSIDE_LEAN_GATE = {
    "c": "C",
    "java": "Java",
    "kotlin": "Kotlin",
    "csharp": "C#",
    "rust": "Rust",
    "swift": "Swift",
}


def _community_locations(stem: str) -> Tuple[str, ...]:
    """Every place and extension GitHub recognises for one community file, ``.md`` at the root first."""
    return tuple(f"{folder}{stem}{ext}" for folder in ("", ".github/", "docs/") for ext in (".md", ".rst", ".txt"))


SECURITY_LOCATIONS = _community_locations("SECURITY")
CONTRIBUTING_LOCATIONS = _community_locations("CONTRIBUTING")
CONDUCT_LOCATIONS = _community_locations("CODE_OF_CONDUCT")
PR_TEMPLATE_LOCATIONS = (
    ".github/PULL_REQUEST_TEMPLATE.md",
    "PULL_REQUEST_TEMPLATE.md",
    "docs/PULL_REQUEST_TEMPLATE.md",
)
DEPENDABOT_LOCATIONS = (".github/dependabot.yml", ".github/dependabot.yaml")
AGENTS_LOCATIONS = ("AGENTS.md",)
VERIFY_SCRIPT = "scripts/verify"
ISSUE_TEMPLATE_DIR = ".github/ISSUE_TEMPLATE/"
LEGACY_ISSUE_TEMPLATES = (".github/ISSUE_TEMPLATE.md", "ISSUE_TEMPLATE.md", "docs/ISSUE_TEMPLATE.md")
ISSUE_FORMS = ("config.yml", "bug_report.yml", "feature_request.yml")
QUALITY_CALLER = ".github/workflows/quality.yml"
#: The lean gate runs gate 4 only when this directory exists and gate 6 only when OSV_CONFIG
#: exists, so the baseline seeds both; without them a "zero tolerance" caller silently skips.
OPENGREP_DIR = ".quality/opengrep/"
SEMGREPIGNORE = ".semgrepignore"
OSV_CONFIG = "osv-scanner.toml"
#: Pseudo-path for findings about what gate 6 can see at all (not a file the baseline writes).
GATE6_SCOPE = "(gate 6 scope)"
#: Curated rule files seeded per language present ("general" applies to every repository).
OPENGREP_RULES = (
    ("general-security.yaml", ()),
    ("python-security.yaml", ("python",)),
    ("javascript-security.yaml", ("js", "ts")),
)

#: sha256 of known platform-generated SECURITY.md boilerplate (stripped, LF line endings)
#: that is safe to upgrade. Repo-authored policies never match and are kept untouched.
KNOWN_GENERIC_SECURITY_SHA256 = frozenset(
    {
        # the generic policy rolled out to private fleet repos on 2026-10-21
        "593a754fd22b1952c7de61452cacc8fa05fcc5d1c55d788f5ab4b7dd688f51f2",
    }
)

_DEPENDABOT_HEADER = (
    "# Managed by quality-zero-platform (docs/FLEET-BASELINE.md).\n"
    "# One update rail per manifest tree, for every ecosystem in this repository.\n"
    "# Security updates are grouped per ecosystem so one pull request can clear\n"
    "# every open advisory (the dependency gate fails on ANY known advisory);\n"
    "# minor/patch updates are grouped; majors are never ignored and arrive one\n"
    "# by one. Regenerate with scripts/quality/fleet_baseline.py instead of\n"
    "# hand-editing, so a new manifest can never be left without a rail.\n"
)


@dataclass(frozen=True)
class RepoFacts:
    """What the planner needs to know about one repository checkout."""

    slug: str
    default_branch: str
    private: bool
    paths: Tuple[str, ...]
    upstream: Optional[str] = None  # owner/name of the parent when this repository is a fork

    @property
    def name(self) -> str:
        """The repository name without the owner."""
        return self.slug.split("/", 1)[-1]


@dataclass(frozen=True)
class FileAction:
    """One decision about one baseline file."""

    path: str
    action: str  # create | replace | bump | keep | gap
    reason: str
    content: Optional[str] = None
    executable: bool = False

    @property
    def writes(self) -> bool:
        """Whether applying this action changes the checkout."""
        return self.action in ("create", "replace", "bump")


def _clean(path: str) -> str:
    """Normalize a repo-relative path to forward slashes without a leading './'."""
    cleaned = path.strip().replace("\\", "/")
    return cleaned[2:] if cleaned.startswith("./") else cleaned


def _is_excluded(path: str) -> bool:
    """Whether a path lives under a vendored, generated or fixture directory."""
    return any(part in EXCLUDED_DIRS for part in path.split("/")[:-1])


def _is_requirements_file(name: str, parent: str) -> bool:
    """``requirements*.txt|.in`` anywhere, or any ``.txt|.in`` inside a ``requirements/`` directory."""
    if not name.endswith((".txt", ".in")):
        return False
    return name.startswith("requirements") or posixpath.basename(parent) == "requirements"


def _ecosystem_for(path: str) -> Optional[str]:
    """Return the Dependabot ecosystem a manifest path belongs to, if any."""
    name = posixpath.basename(path)
    parent = posixpath.dirname(path)
    if (parent == ".github/workflows" and name.endswith((".yml", ".yaml"))) or name in ("action.yml", "action.yaml"):
        return "github-actions"
    if name in _PIP_NAMES or _is_requirements_file(name, parent):
        return "pip"
    if name in _GRADLE_NAMES:
        return "gradle"
    if name.endswith(_NUGET_SUFFIXES) or name in _NUGET_NAMES:
        return "nuget"
    if name in _COMPOSE_NAMES:
        return "docker-compose"
    if name in ("Dockerfile", "Containerfile") or name.startswith("Dockerfile.") or name.endswith(".Dockerfile"):
        return "docker"
    if name.endswith(".tf"):
        return "terraform"
    if path == ".gitmodules":
        return "gitsubmodule"
    if path in (".devcontainer.json", ".devcontainer/devcontainer.json"):
        return "devcontainers"
    return _SIMPLE_MANIFESTS.get(name)


def _rail_dir(ecosystem: str, path: str) -> str:
    """Return the Dependabot ``directory`` that updates the manifest at ``path``."""
    parent = posixpath.dirname(path)
    if ecosystem in _ROOT_RAILS or (ecosystem == "github-actions" and parent == ".github/workflows"):
        return "/"
    if ecosystem == "pip" and posixpath.basename(parent) == "requirements":
        # Dependabot's pip updater reads requirements/*.txt from the enclosing directory.
        parent = posixpath.dirname(parent)
    return "/" + parent if parent else "/"


def _is_ancestor(ancestor: str, directory: str) -> bool:
    """Whether ``ancestor`` strictly contains ``directory`` (Dependabot directory form)."""
    if ancestor == directory:
        return False
    return ancestor == "/" or directory.startswith(ancestor.rstrip("/") + "/")


_BUILD_COMMENT_RE = re.compile(r"<!--.*?-->|/\*.*?\*/|//[^\n]*", re.DOTALL)


_MAVEN_MODULE_RE = re.compile(r"<module>\s*([^<]+?)\s*</module>")
_GRADLE_INCLUDE_RE = re.compile(r"\binclude\b\s*\(?([^)\n]*)")
_GRADLE_PROJECT_RE = re.compile(r"[\"']:?([^\"']+)[\"']")
_SLN_PROJECT_RE = re.compile(r'^Project\("[^"]*"\)\s*=\s*"[^"]*",\s*"([^"]+)"', re.MULTILINE)


def declared_modules(ecosystem: str, text: str) -> Set[str]:
    """The nested module directories a root build declares, in ``a/b`` form.

    Only each ecosystem's declaration syntax counts (Maven ``<module>``, Gradle ``include``,
    ``.sln`` ``Project(...)`` entries), and comments are stripped first, so a dependency that
    merely shares a directory's name, or a commented-out include, declares nothing.
    """
    text = _BUILD_COMMENT_RE.sub("", text)
    if ecosystem == "maven":
        return {m.strip().strip("/").removeprefix("./") for m in _MAVEN_MODULE_RE.findall(text)}
    if ecosystem == "gradle":
        found: Set[str] = set()
        for args in _GRADLE_INCLUDE_RE.findall(text):
            found.update(name.strip(":").replace(":", "/") for name in _GRADLE_PROJECT_RE.findall(args))
        return found
    return {posixpath.dirname(path.replace("\\", "/")) for path in _SLN_PROJECT_RE.findall(text)}


def _build_text(ecosystem: str, directory: str, paths: Sequence[str], read: Reader) -> str:
    """Concatenate the build files that can declare nested modules for ``directory``."""
    prefix = "" if directory == "/" else directory.strip("/") + "/"
    if ecosystem == "nuget":
        names = [p for p in paths if p.startswith(prefix) and "/" not in p[len(prefix) :] and p.endswith(".sln")]
    else:
        names = [prefix + name for name in _ROOT_BUILD_FILES[ecosystem]]
    return "\n".join(read(name) or "" for name in names)


def _drop_declared(ecosystem: str, directories: Set[str], paths: Sequence[str], read: Optional[Reader]) -> Set[str]:
    """Drop nested directories an enclosing rail's build file declares as its own module.

    Without a reader nothing is dropped: a duplicate rail is noise, a missing one is a blind spot.
    """
    if read is None or ecosystem not in ("gradle", "maven", "nuget"):
        return directories
    kept = set(directories)
    for nested in directories:
        for ancestor in directories:
            if not _is_ancestor(ancestor, nested):
                continue
            relative = nested[len(ancestor.rstrip("/")) :].strip("/")
            if relative in declared_modules(ecosystem, _build_text(ecosystem, ancestor, paths, read)):
                kept.discard(nested)
                break
    return kept


def _dir_sort_key(directory: str) -> Tuple[bool, str]:
    """Sort the repository root first, then alphabetically."""
    return (directory != "/", directory)


def detect_rails(paths: Iterable[str], read: Optional[Reader] = None) -> Dict[str, List[str]]:
    """Map every Dependabot ecosystem present in ``paths`` to its manifest directories."""
    cleaned = [_clean(raw) for raw in paths]
    found: Dict[str, Set[str]] = {}
    for path in cleaned:
        if not path or _is_excluded(path):
            continue
        ecosystem = _ecosystem_for(path)
        if ecosystem is None:
            continue
        found.setdefault(ecosystem, set()).add(_rail_dir(ecosystem, path))
    for winner, loser in _SUPERSEDES:
        for directory in found.get(winner, set()):
            found.get(loser, set()).discard(directory)
    rails: Dict[str, List[str]] = {}
    for ecosystem in sorted(found):
        directories = _drop_declared(ecosystem, found[ecosystem], cleaned, read)
        if directories:
            rails[ecosystem] = sorted(directories, key=_dir_sort_key)
    return rails


def _group_key(ecosystem: str) -> str:
    """A Dependabot-safe group-name prefix for an ecosystem."""
    return re.sub(r"[^a-z0-9]+", "-", ecosystem.lower()).strip("-")


def render_dependabot(rails: Dict[str, List[str]]) -> str:
    """Render ``.github/dependabot.yml`` for the given rails (zero-tolerance policy)."""
    updates: List[Dict[str, object]] = []
    for ecosystem, directories in rails.items():
        key = _group_key(ecosystem)
        entry: Dict[str, object] = {"package-ecosystem": ecosystem}
        if len(directories) == 1:
            entry["directory"] = directories[0]
        else:
            entry["directories"] = list(directories)
        version_group: Dict[str, object] = {"applies-to": "version-updates", "patterns": ["*"]}
        if ecosystem not in _NO_SEMVER:
            version_group["update-types"] = ["minor", "patch"]
        entry.update(
            {
                "schedule": {"interval": "weekly"},
                "open-pull-requests-limit": 10,
                "labels": ["dependencies"],
                "groups": {
                    f"{key}-security": {"applies-to": "security-updates", "patterns": ["*"]},
                    f"{key}-minor-patch": version_group,
                },
            }
        )
        updates.append(entry)
    body = yaml.safe_dump({"version": 2, "updates": updates}, sort_keys=False, default_flow_style=False)
    return _DEPENDABOT_HEADER + body


def _normalize_dir(directory: str) -> str:
    """Normalize a Dependabot directory (or glob) to ``/a/b`` form."""
    stripped = directory.strip().strip("/")
    return "/" + stripped if stripped else "/"


def uncovered_rails(rails: Dict[str, List[str]], config_text: str) -> List[Tuple[str, str]]:
    """Return ``(ecosystem, directory)`` rails an existing dependabot.yml does not update.

    An unparseable or non-mapping config covers nothing. ``directories`` globs are
    honored (``fnmatch`` semantics, slightly more permissive than Dependabot's). An
    entry GitHub would reject — ``directories`` that is not a list of strings, or a
    ``directory`` that is not a string — covers nothing either.
    """
    try:
        document = yaml.safe_load(config_text)
    except yaml.YAMLError:
        document = None
    covered: Dict[str, List[str]] = {}
    updates = document.get("updates") if isinstance(document, dict) else None
    for update in updates if isinstance(updates, list) else []:
        if not isinstance(update, dict):
            continue
        schedule = update.get("schedule")
        if not (isinstance(schedule, dict) and schedule.get("interval")):
            continue
        directories = update.get("directories", [update.get("directory")])
        if not isinstance(directories, list) or not all(isinstance(item, str) for item in directories):
            continue
        covered.setdefault(str(update.get("package-ecosystem", "")), []).extend(
            _normalize_dir(item) for item in directories if item
        )
    missing: List[Tuple[str, str]] = []
    for ecosystem, directories in rails.items():
        patterns = covered.get(ecosystem, [])
        for directory in directories:
            target = _normalize_dir(directory)
            if not any(target == pattern or fnmatch.fnmatchcase(target, pattern) for pattern in patterns):
                missing.append((ecosystem, directory))
    return missing


def detect_languages(paths: Iterable[str]) -> Tuple[str, ...]:
    """Return the languages present in ``paths`` (keys of ``LANGUAGE_EXTENSIONS``)."""
    present: Set[str] = set()
    for raw in paths:
        path = _clean(raw)
        if not path or _is_excluded(path):
            continue
        extension = posixpath.splitext(path)[1]
        for language, extensions in LANGUAGE_EXTENSIONS.items():
            if extension in extensions:
                present.add(language)
    return tuple(language for language in LANGUAGE_EXTENSIONS if language in present)


def _has_jsts(languages: Sequence[str]) -> bool:
    """Whether the lean gate's JS/TS lanes run for these languages."""
    return "ts" in languages or "js" in languages


def gate_rows(languages: Sequence[str], deps_floor: str = "t0") -> List[Tuple[str, str, str]]:
    """Return ``(gate, what runs, passes when)`` rows for the lean gate."""
    python, jsts, typed_ts = "python" in languages, _has_jsts(languages), "ts" in languages
    lint = [
        label
        for flag, label in (
            (python, "ruff check + ruff format --check"),
            (jsts, "oxlint --deny-warnings + biome format check"),
        )
        if flag
    ]
    types = [
        label
        for flag, label in ((python, "basedpyright (standard mode)"), (typed_ts, "tsc --noEmit for every tsconfig"))
        if flag
    ]
    tests = [
        label
        for flag, label in (
            (python, "the Python test suite under branch coverage"),
            (jsts, "the `test:coverage` npm script + a coverage-number assert"),
            ("go" in languages or "cpp" in languages, "`scripts/coverage-gate.sh` (Go / C++)"),
        )
        if flag
    ]
    if deps_floor == "any":
        deps_bar = "zero known advisories, any severity"
    else:
        deps_bar = "zero advisories at or above the T0 floor (CVSS >= 7.0 with a published fix); the rest is listed"
    none = "n/a — no such source in this repository"
    return [
        ("1 · lint + format", "; ".join(lint) or none, "zero findings and zero format diffs"),
        ("2 · types", "; ".join(types) or none, "zero errors **and** zero warnings"),
        ("3 · tests + coverage", "; ".join(tests) or none, "every test passes at 100 % line **and** branch"),
        ("4 · SAST", "opengrep with the curated `.quality/opengrep` ruleset (seeded from QZP)", "zero findings"),
        ("5 · secrets", "gitleaks", "zero findings"),
        ("6 · dependencies", "osv-scanner on every lockfile and manifest (`osv-scanner.toml`)", deps_bar),
    ]


def _python_uses_pytest(read: Reader) -> bool:
    """Whether the lean gate runs pytest (else unittest): its exact rule, 'pytest' in a root config file."""
    return any("pytest" in (read(name) or "") for name in ("pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini"))


def _coverage_scripts(read: Reader, paths: Sequence[str]) -> List[str]:
    """Return the directories (``.`` = root) whose package.json has a ``test:coverage`` script."""
    found: List[str] = []
    for path in sorted(p for p in paths if posixpath.basename(p) == "package.json" and not _is_excluded(p)):
        if path.count("/") > 1:
            continue
        try:
            manifest = json.loads(read(path) or "{}")
        except ValueError:
            continue
        scripts = manifest.get("scripts") if isinstance(manifest, dict) else None
        if isinstance(scripts, dict) and "test:coverage" in scripts:
            found.append(posixpath.dirname(path) or ".")
    return found


def local_commands(languages: Sequence[str], read: Reader, paths: Sequence[str]) -> str:
    """Render the language-specific 'run the tests yourself' bullets for CONTRIBUTING.md."""
    lines: List[str] = []
    if "python" in languages:
        if _python_uses_pytest(read):
            lines.append("- Python: `python -m pytest --cov --cov-branch --cov-report=term-missing`")
        else:
            lines.append(
                "- Python: `python -m coverage run --branch -m unittest discover` then "
                "`python -m coverage report --fail-under=100 --show-missing`"
            )
    if _has_jsts(languages):
        directories = _coverage_scripts(read, paths)
        if directories:
            lines.extend(f"- JS/TS (`{directory}`): `npm ci && npm run test:coverage`" for directory in directories)
        else:
            lines.append("- JS/TS: add a `test:coverage` npm script — the coverage gate runs it and asserts 100 %")
    if "go" in languages or "cpp" in languages:
        lines.append("- Go / C++: `bash scripts/coverage-gate.sh` (the script the coverage gate runs)")
    if not lines:
        return ""
    return "Language-specific test commands for quick iteration:\n\n" + "\n".join(lines)


def _private_reporting(facts: RepoFacts) -> str:
    """The private reporting URL: PVR for public repos; for private ones their own issues.

    A private repository's issues are visible only to people with access to it, so they are
    an actionable private channel there (a GitHub profile has no reporting form).
    """
    if facts.private:
        return f"https://github.com/{facts.slug}/issues/new?labels=security"
    return f"https://github.com/{facts.slug}/security/advisories/new"


def _fork_security_note(facts: RepoFacts) -> str:
    """Where a fork's vulnerabilities belong: upstream code upstream, the fork's own changes here."""
    if not facts.upstream:
        return ""
    return (
        f"This repository is a fork of [{facts.upstream}](https://github.com/{facts.upstream}). Report a "
        "vulnerability in code inherited from upstream to the upstream project through its own security policy; "
        "report one introduced by this fork's changes here."
    )


def _fork_contributing_note(facts: RepoFacts) -> str:
    """A fork's contribution note: changes that belong upstream should go upstream too."""
    if not facts.upstream:
        return ""
    return (
        f"This repository is a fork of [{facts.upstream}](https://github.com/{facts.upstream}). Changes that are "
        "useful beyond this fork belong upstream as well; keep fork-only changes small and easy to rebase."
    )


def _first_present(paths: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    """Return the checkout's spelling of the first candidate present, matched case-insensitively.

    GitHub recognises community files case-insensitively and macOS / Windows checkouts are
    case-insensitive too, so ``security.md`` must count as an existing ``SECURITY.md``.
    """
    present: Dict[str, str] = {}
    for path in paths:
        present.setdefault(path.lower(), path)
    return next((present[c.lower()] for c in candidates if c.lower() in present), None)


def _codeql_suite(text: str) -> str:
    """What a CodeQL workflow's ``queries:`` selects, ignoring comments.

    ``default`` when it selects nothing and has no config; ``configured`` when the queries
    come from a list or a referenced config the planner cannot see; else the value itself.
    """
    live = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    match = _CODEQL_QUERIES_RE.search(live)
    if match is None:
        return "configured" if _CODEQL_CONFIG_RE.search(live) else "default"
    value = match.group(1).split(" #", 1)[0].strip().strip("'\"")
    # empty (a list follows) or a block scalar (`|` / `>`): the queries are configured, not named
    return value if value and value[0] not in "|>" else "configured"


def _workflow_uses(document: object) -> List[Tuple[str, str]]:
    """Every ``(uses, step-or-job text)`` a parsed workflow runs: job-level and step-level ``uses``."""
    found: List[Tuple[str, str]] = []
    jobs = document.get("jobs") if isinstance(document, dict) else None
    for job in jobs.values() if isinstance(jobs, dict) else []:
        if not isinstance(job, dict):
            continue
        if isinstance(job.get("uses"), str):
            found.append((job["uses"], ""))
        steps = job.get("steps")
        for step in steps if isinstance(steps, list) else []:
            if isinstance(step, dict) and isinstance(step.get("uses"), str):
                found.append((step["uses"], yaml.safe_dump(step.get("with") or {}, sort_keys=False)))
    return found


def codeql_workflow(facts: RepoFacts, read: Reader) -> Optional[Tuple[str, Optional[str], bool]]:
    """The checked-in CodeQL workflow: ``(path, query suite, runs through QZP's reusable-codeql.yml)``.

    ``None`` means no workflow runs CodeQL, so the repository relies on GitHub default setup.
    Only parsed job and step ``uses`` values count, never comments or shell text. A QZP
    caller's suite is ``None``: it is whatever ``reusable-codeql.yml`` selects at the commit
    the caller pins, which the planner cannot see.
    """
    workflows = sorted(
        p for p in facts.paths if posixpath.dirname(p) == ".github/workflows" and p.endswith((".yml", ".yaml"))
    )
    for path in workflows:
        try:
            document = yaml.safe_load(read(path) or "")
        except yaml.YAMLError:
            continue
        for uses, inputs in _workflow_uses(document):
            if _REUSABLE_CODEQL_RE.match(uses):
                return path, None, True
            if _CODEQL_INIT_RE.match(uses):
                return path, _codeql_suite(inputs), False
    return None


def _codeql_control(facts: RepoFacts, read: Reader) -> str:
    """The CodeQL bullet, describing the setup the repository actually has."""
    if facts.private:
        return (
            "- **Code scanning** — CodeQL needs GitHub Advanced Security on private repositories; until it is "
            "available the opengrep SAST gate is the code-scanning control here."
        )
    merge = "a pull request that introduces a code-scanning alert of any severity cannot be merged."
    found = codeql_workflow(facts, read)
    if found is None:
        return (
            "- **CodeQL** — GitHub default setup with the *extended* query suite and the remote + local threat "
            f"model, on every pull request and weekly; {merge}"
        )
    path, suite, through_qzp = found
    if through_qzp:
        runner = f"`{path}` calls quality-zero-platform's `reusable-codeql.yml` at the commit it pins"
    elif suite == "default":
        runner = f"`{path}` runs the default query suite"
    elif suite == "configured":
        runner = f"`{path}` runs the queries its configuration lists"
    else:
        runner = f"`{path}` runs the `{suite}` queries"
    return f"- **CodeQL** — advanced setup: {runner}; {merge}"


def _verify_is_managed(facts: RepoFacts, read: Reader) -> bool:
    """Whether scripts/verify is (or will be) the baseline's managed QZP-preflight wrapper."""
    return VERIFY_SCRIPT not in facts.paths or MANAGED_VERIFY_MARKER in (read(VERIFY_SCRIPT) or "")


def _dependabot_config(facts: RepoFacts, read: Reader) -> Optional[str]:
    """The repository-authored Dependabot config, or ``None`` when the baseline manages (or creates) it."""
    existing = _first_present(facts.paths, DEPENDABOT_LOCATIONS)
    if existing is None or (read(existing) or "").replace("\r\n", "\n").startswith(_DEPENDABOT_HEADER):
        return None
    return existing


_MANAGED_VERIFY_SECTION = (
    "```bash\nbash scripts/verify\n```\n\n"
    "`scripts/verify` runs QZP's preflight at the exact commit that\n"
    "`.github/workflows/quality.yml` pins, so every CI lane runs locally with the\n"
    "same pinned tool versions. Run it from this repository's root before every\n"
    "push. A lane that cannot run locally (for example a missing toolchain) is\n"
    "reported as `SKIP`, never as a silent pass — CI will still run it."
)
_OWN_VERIFY_SECTION = (
    "```bash\nbash scripts/verify\n```\n\n"
    "`scripts/verify` is this repository's own verification script: run it from\n"
    "the repository root before every push. It is not the QZP lean gate — CI runs\n"
    "every lean-gate lane on each pull request, and you can run them locally with\n"
    "quality-zero-platform's `scripts/quality/preflight.sh`, checked out at the\n"
    "commit `.github/workflows/quality.yml` pins."
)
_MANAGED_AGENTS_VERIFY = (
    "- **Verify before claiming completion:** run `bash scripts/verify`. It runs\n"
    "  every lane of the QZP lean gate at the exact commit CI pins. A finding of\n"
    "  any severity is a failure."
)
_OWN_AGENTS_VERIFY = (
    "- **Verify before claiming completion:** run `bash scripts/verify` (this\n"
    "  repository's own checks) and confirm that every CI lane, the QZP lean gate\n"
    "  included, is green. A finding of any severity is a failure."
)
_MANAGED_DEPENDENCIES = (
    "Dependabot proposes updates weekly: minor and patch updates are grouped per\n"
    "ecosystem, security updates are grouped so one pull request can clear every\n"
    "open advisory, and major updates arrive one by one for review. Because the\n"
    "dependency gate fails on *any* known advisory, keep lockfiles current — an\n"
    "unmerged security update turns every other pull request red too."
)
_OWN_DEPENDENCIES = (
    "Dependabot keeps dependencies current on the schedule set in\n"
    "`{path}`. Because the dependency gate fails on *any* known\n"
    "advisory, merge security updates promptly — an unmerged security update\n"
    "turns every other pull request red too."
)


def template_context(facts: RepoFacts, read: Reader, qzp_pin: str, deps_floor: str = "t0") -> Dict[str, str]:
    """Build the substitution context every fleet template renders against.

    Every statement the documents make is about this repository as it is, or as this
    baseline run leaves it: a checked-in CodeQL workflow is described as advanced setup,
    and a repository-authored ``scripts/verify``, pull-request template or Dependabot
    config is described as the repository's own rather than as the managed one.
    """
    languages = detect_languages(facts.paths)
    rows = gate_rows(languages, deps_floor)
    report_url = _private_reporting(facts)
    policy_path = _first_present(facts.paths, SECURITY_LOCATIONS) or "SECURITY.md"
    if facts.private:
        reporting = (
            "This repository is private, so its issues are visible only to people with access to it: "
            f"report through a [new issue labelled `security`]({report_url}). If a finding must not reach "
            "every collaborator, say so in that issue without the details and the maintainer will arrange a "
            "direct channel."
        )
        # GitHub has no private reporting form for private repositories: the issue form links the policy.
        contact_url = f"https://github.com/{facts.slug}/blob/{facts.default_branch}/{policy_path}"
        conduct = (
            "Report conduct violations to the maintainer; this repository is private, so its issues reach only "
            "people with access to it, and the maintainer will arrange a direct channel on request."
        )
    else:
        reporting = (
            f"**Please use [private vulnerability reporting]({report_url})** — the *Report a vulnerability* "
            "button in this repository's *Security* tab. Reports there go privately to the maintainer and allow "
            "coordinated disclosure. If that form is unavailable to you, contact the maintainer through their "
            f"[GitHub profile]({MAINTAINER_URL}) without any details and ask for a private channel."
        )
        contact_url = report_url
        conduct = (
            f"Report conduct violations privately via [private vulnerability reporting]({report_url}) (a private "
            f"channel to the maintainer) or through the maintainer's [GitHub profile]({MAINTAINER_URL})."
        )
    codeql = _codeql_control(facts, read)
    gate = "in the lean gate (`quality / quality`)"
    secrets = f"- **Secrets** — gitleaks {gate}" + (
        "." if facts.private else "; GitHub secret scanning with push protection is enabled."
    )
    own_dependabot = _dependabot_config(facts, read)
    dependencies = "- **Dependencies** — osv-scanner over every lockfile it can read, plus Dependabot alerts and " + (
        "security updates." if own_dependabot else "grouped security updates."
    )
    controls = [codeql, f"- **SAST** — opengrep {gate}.", secrets, dependencies]
    outside = [label for key, label in _OUTSIDE_LEAN_GATE.items() if key in languages]
    if own_dependabot:
        dependabot = f"- **Dependabot** — `{own_dependabot}` (this repository's own configuration) proposes updates."
    else:
        dependabot = "- **Dependabot** — `.github/dependabot.yml` keeps every manifest tree on a weekly update rail."
    extra = [codeql, dependabot]
    if outside:
        extra.append(
            f"- **{', '.join(outside)}** — not measured by the lean gate; this repository's own CI workflow(s) "
            "test it and must be green too."
        )
    checklist = [f"- [ ] **gate {gate}** — {what}: {passes}" for gate, what, passes in rows]
    managed_verify = _verify_is_managed(facts, read)
    own_pr_template = _first_present(facts.paths, PR_TEMPLATE_LOCATIONS) is not None
    return {
        "repo_slug": facts.slug,
        "fork_security_note": _fork_security_note(facts),
        "fork_contributing_note": _fork_contributing_note(facts),
        "display_name": facts.name,
        "default_branch": facts.default_branch,
        "reporting_channel": reporting,
        "security_controls": "\n".join(controls),
        "security_contact_url": contact_url,
        "security_policy_path": policy_path,
        "conduct_policy_path": _first_present(facts.paths, CONDUCT_LOCATIONS) or "CODE_OF_CONDUCT.md",
        "conduct_contact": conduct,
        "gate_rows": "\n".join(f"| {gate} | {what} | {passes} |" for gate, what, passes in rows),
        "gate_checklist": "\n".join(checklist),
        "extra_controls": "\n".join(extra),
        "local_commands": local_commands(languages, read, facts.paths),
        "verify_section": _MANAGED_VERIFY_SECTION if managed_verify else _OWN_VERIFY_SECTION,
        "agents_verify": _MANAGED_AGENTS_VERIFY if managed_verify else _OWN_AGENTS_VERIFY,
        "pr_template_step": (
            "Open a pull request and fill in the pull-request template."
            if own_pr_template
            else "Open a pull request and fill in the checklist from the pull-request\n"
            "   template; it maps 1:1 to what CI enforces."
        ),
        "dependencies_section": (
            _OWN_DEPENDENCIES.format(path=own_dependabot) if own_dependabot else _MANAGED_DEPENDENCIES
        ),
        "qzp_pin": qzp_pin,
        "caller_with": caller_with({}, deps_floor),
        "caller_triggers": render_triggers((facts.default_branch,), (facts.default_branch,), False),
        "deps_floor": deps_floor,
    }


def render_template(name: str, context: Dict[str, str], *, root: Path = TEMPLATES_ROOT) -> str:
    """Render ``templates/fleet/<name>.tmpl`` with ``string.Template`` (missing keys raise)."""
    raw = (root / f"{name}.tmpl").read_text(encoding="utf-8")
    rendered = string.Template(raw).substitute(context)
    return re.sub(r"\n{3,}", "\n\n", rendered).rstrip("\n") + "\n"


def _stripped_sha256(text: str) -> str:
    """sha256 of text with LF line endings and surrounding whitespace removed."""
    return hashlib.sha256(text.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


def is_upgradable_security_policy(text: str, slug: str) -> bool:
    """Whether an existing SECURITY.md is platform boilerplate the baseline may replace."""
    if _stripped_sha256(text) in KNOWN_GENERIC_SECURITY_SHA256:
        return True
    legacy = render_legacy_security_policy({"slug": slug})
    return text.replace("\r\n", "\n").strip() == legacy.strip()


def _plan_security(facts: RepoFacts, read: Reader, context: Dict[str, str]) -> FileAction:
    """Decide what to do about SECURITY.md."""
    content = render_template("SECURITY.md", context)
    existing = _first_present(facts.paths, SECURITY_LOCATIONS)
    if existing is None:
        return FileAction("SECURITY.md", "create", "no security policy in the repository", content)
    text = read(existing) or ""
    if existing == "SECURITY.md" and is_upgradable_security_policy(text, facts.slug):
        return FileAction("SECURITY.md", "replace", "platform boilerplate upgraded to the fleet policy", content)
    return FileAction(existing, "keep", "repository-authored security policy")


def _plan_simple(
    facts: RepoFacts,
    candidates: Sequence[str],
    target: str,
    template: str,
    context: Dict[str, str],
    *,
    executable: bool = False,
) -> FileAction:
    """Create ``target`` from ``template`` unless any candidate location already exists."""
    existing = _first_present(facts.paths, candidates)
    if existing is not None:
        return FileAction(existing, "keep", "repository-authored file")
    return FileAction(target, "create", "missing", render_template(template, context), executable)


def _plan_verify(facts: RepoFacts, read: Reader, context: Dict[str, str]) -> FileAction:
    """Create scripts/verify, upgrade an outdated managed one, or keep the repository's own."""
    rendered = render_template("verify", context)
    if VERIFY_SCRIPT not in facts.paths:
        return FileAction(VERIFY_SCRIPT, "create", "missing", rendered, executable=True)
    text = (read(VERIFY_SCRIPT) or "").replace("\r\n", "\n")
    if MANAGED_VERIFY_MARKER not in text:
        return FileAction(VERIFY_SCRIPT, "keep", "repository-authored file")
    if text == rendered:
        return FileAction(VERIFY_SCRIPT, "keep", "managed verify is current")
    return FileAction(VERIFY_SCRIPT, "replace", "managed verify upgraded to the current template", rendered, True)


def _plan_issue_forms(facts: RepoFacts, context: Dict[str, str]) -> List[FileAction]:
    """Create the issue forms unless the repository already ships issue templates of its own."""
    if any(path.lower().startswith(ISSUE_TEMPLATE_DIR.lower()) for path in facts.paths):
        return [FileAction(ISSUE_TEMPLATE_DIR, "keep", "repository-authored issue templates")]
    legacy = _first_present(facts.paths, LEGACY_ISSUE_TEMPLATES)
    if legacy is not None:
        return [FileAction(legacy, "keep", "repository-authored issue template")]
    return [
        FileAction(ISSUE_TEMPLATE_DIR + form, "create", "missing", render_template("ISSUE_TEMPLATE/" + form, context))
        for form in ISSUE_FORMS
    ]


def _seeded_rules_only(rule_files: Sequence[str], read: Reader) -> bool:
    """Whether every rule file is an unmodified copy of a QZP-seeded rule (managed, may grow)."""
    seeded = {OPENGREP_DIR + name: name for name, _needs in OPENGREP_RULES}
    for path in rule_files:
        if path not in seeded:
            return False
        source = (OPENGREP_SOURCE / seeded[path]).read_text(encoding="utf-8")
        if (read(path) or "").replace("\r\n", "\n") != source:
            return False
    return True


#: .semgrepignore entries that drop first-party code from gate 4 (everything, or the test trees).
_FORBIDDEN_SCOPE_ENTRIES = frozenset(
    {"*", "**", "/", "**/*", "./", "test/", "tests/", "/test/", "/tests/", "**/test/", "**/tests/"}
    | {"spec/", "**/spec/", "__tests__/", "**/__tests__/", "*_test.go", "**/*_test.go", "test", "tests"}
)


def _scope_gaps(text: str) -> List[str]:
    """Entries of a repository-authored .semgrepignore that exclude first-party source or tests."""
    entries = [line.strip() for line in text.splitlines()]
    return [entry for entry in entries if entry in _FORBIDDEN_SCOPE_ENTRIES]


def osv_ignore_problems(config_text: str, today: datetime.date) -> List[str]:
    """Why an osv-scanner.toml's ignores cannot be trusted: unparseable, unreasoned, open-ended or expired."""
    try:
        document = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError:
        return ["osv-scanner.toml does not parse, so gate 6 fails with exit 130"]
    problems: List[str] = []
    entries = document.get("IgnoredVulns", [])
    for entry in entries if isinstance(entries, list) else [entries]:
        entry = entry if isinstance(entry, dict) else {}
        ident = str(entry.get("id", "?"))
        until = entry.get("ignoreUntil")
        if not str(entry.get("reason", "")).strip():
            problems.append(f"ignore {ident} has no reason")
        if not isinstance(until, (datetime.date, datetime.datetime)):
            problems.append(f"ignore {ident} has no ignoreUntil date")
        elif (until.date() if isinstance(until, datetime.datetime) else until) < today:
            problems.append(f"ignore {ident} expired on {until}")
    return problems


def _plan_gate_configs(
    facts: RepoFacts, read: Reader, context: Dict[str, str], today: Optional[datetime.date] = None
) -> List[FileAction]:
    """Seed the gate-4 ruleset + scope file and the gate-6 config so neither gate silently skips."""
    today = today or datetime.date.today()
    languages = set(detect_languages(facts.paths))
    actions: List[FileAction] = []
    rule_files = [p for p in facts.paths if p.startswith(OPENGREP_DIR) and p.endswith((".yml", ".yaml"))]
    if rule_files and not _seeded_rules_only(rule_files, read):
        actions.append(FileAction(OPENGREP_DIR, "keep", "repository-curated SAST ruleset"))
    else:
        if _first_present(facts.paths, (OPENGREP_DIR + "README.md",)) is None:
            actions.append(
                FileAction(
                    OPENGREP_DIR + "README.md",
                    "create",
                    "gate 4 has no ruleset",
                    render_template("opengrep-README.md", context),
                )
            )
        actions.extend(
            FileAction(
                OPENGREP_DIR + name,
                "create",
                "gate 4 has no rule for this language" if rule_files else "gate 4 has no ruleset",
                (OPENGREP_SOURCE / name).read_text(encoding="utf-8"),
            )
            for name, needs in OPENGREP_RULES
            if (not needs or languages.intersection(needs)) and OPENGREP_DIR + name not in rule_files
        )
    # gate 4 and gate 6 test these exact paths on a case-sensitive runner, so no case folding here
    if SEMGREPIGNORE in facts.paths:
        dropped = _scope_gaps(read(SEMGREPIGNORE) or "")
        if dropped:
            reason = f"excludes first-party code from gate 4: {', '.join(dropped)}"
            actions.append(FileAction(SEMGREPIGNORE, "gap", reason))
        else:
            actions.append(FileAction(SEMGREPIGNORE, "keep", "repository-chosen SAST scope"))
    else:
        actions.append(
            FileAction(
                SEMGREPIGNORE,
                "create",
                "without it opengrep silently skips test trees",
                SEMGREPIGNORE_SOURCE.read_text(encoding="utf-8"),
            )
        )
    if OSV_CONFIG in facts.paths:
        problems = osv_ignore_problems(read(OSV_CONFIG) or "", today)
        if problems:
            actions.append(FileAction(OSV_CONFIG, "gap", "; ".join(problems)))
        else:
            actions.append(FileAction(OSV_CONFIG, "keep", "repository osv-scanner config"))
    else:
        actions.append(FileAction(OSV_CONFIG, "create", "gate 6 has no config", render_template(OSV_CONFIG, context)))
    return actions


def _plan_dependabot(facts: RepoFacts, read: Reader, planned: Sequence[str] = ()) -> FileAction:
    """Create dependabot.yml, regenerate a managed one, or audit a repository-authored one.

    ``planned`` are paths this run is about to create (the lean-gate caller), so a
    repository that gains its first workflow also gains its ``github-actions`` rail.
    """
    rails = detect_rails(list(facts.paths) + list(planned), read)
    existing = _first_present(facts.paths, DEPENDABOT_LOCATIONS)
    if existing is None:
        if not rails:
            return FileAction(DEPENDABOT_LOCATIONS[0], "keep", "no manifests or workflows to update")
        return FileAction(DEPENDABOT_LOCATIONS[0], "create", "missing", render_dependabot(rails))
    text = (read(existing) or "").replace("\r\n", "\n")
    if text.startswith(_DEPENDABOT_HEADER):
        rendered = render_dependabot(rails)
        if text == rendered:
            return FileAction(existing, "keep", "managed config is current")
        return FileAction(existing, "replace", "managed config regenerated for the current manifests", rendered)
    problems = []
    missing = uncovered_rails(rails, text)
    if missing:
        listed = ", ".join(f"{ecosystem} {directory}" for ecosystem, directory in missing)
        problems.append(f"manifest trees without a Dependabot rail: {listed}")
    if ignores_majors(text):
        problems.append("semver-major updates are ignored, so a fix that only ships in a new major never arrives")
    if not problems:
        return FileAction(existing, "keep", "every manifest tree has a rail")
    return FileAction(existing, "gap", "; ".join(problems))


#: What gate 6 can read, per package ecosystem: the lockfiles osv-scanner v2.3.8 extracts in a
#: source scan (docs/supported_languages_and_lockfiles.md at that tag), as fnmatch patterns
#: relative to the rail directory. go.mod and pom.xml are read directly (maven resolves its
#: transitive tree), so those ecosystems always count as scanned.
_SCANNED_LOCKFILES = {
    "npm": ("package-lock.json", "pnpm-lock.yaml", "yarn.lock"),
    "bun": ("bun.lock",),
    "pip": (
        "*requirements*.txt",
        "Pipfile.lock",
        "poetry.lock",
        "pdm.lock",
        "pylock.toml",
    ),
    "uv": ("uv.lock",),
    "cargo": ("Cargo.lock",),
    "composer": ("composer.lock",),
    "bundler": ("Gemfile.lock", "gems.locked"),
    "gradle": ("gradle.lockfile", "buildscript-gradle.lockfile", "gradle/verification-metadata.xml"),
    "nuget": ("packages.lock.json", "packages.config", "*.deps.json"),
    "pub": ("pubspec.lock",),
    "mix": ("mix.lock",),
}
#: Package ecosystems osv-scanner has no extractor for at all.
_UNSCANNED_ECOSYSTEMS = {"swift": "Swift packages"}
#: Ecosystems whose root build folds declared modules into its own rail (see ``_drop_declared``):
#: a lockfile in such a module counts for the root. Everywhere else a lockfile must sit in the
#: rail's own directory (pip also reads a ``requirements/`` subdirectory).
_FOLDING_ECOSYSTEMS = frozenset({"gradle", "maven", "nuget"})
#: Workspace roots whose members share the root lockfile: (manifest, TOML path to the workspace table).
_WORKSPACE_TABLES = {"cargo": ("Cargo.toml", ("workspace",)), "uv": ("pyproject.toml", ("tool", "uv", "workspace"))}


def _rail_of(path: str, directories: Sequence[str]) -> Optional[str]:
    """The deepest rail directory that contains the file at ``path`` (``None`` when none does)."""
    owners = [d for d in directories if d == "/" or path.startswith(d.strip("/") + "/")]
    return max(owners, key=len) if owners else None


def _readable(path: str, lockfiles: Sequence[str]) -> bool:
    """Whether the file at ``path`` is one osv-scanner reads: its trailing components match a pattern."""
    parts = path.split("/")
    return any(
        len(parts) > pattern.count("/") and fnmatch.fnmatchcase("/".join(parts[-(pattern.count("/") + 1) :]), pattern)
        for pattern in lockfiles
    )


def _lockfile_rail(ecosystem: str, path: str, directories: Sequence[str]) -> Optional[str]:
    """The rail a readable lockfile covers: its own directory's rail, or a folding root's."""
    owner = _rail_of(path, directories)
    if owner is None or ecosystem in _FOLDING_ECOSYSTEMS:
        return owner
    relative = path if owner == "/" else path[len(owner.strip("/")) + 1 :]
    depth = relative.count("/")
    if depth == 0 or (depth == 1 and ecosystem == "pip" and relative.startswith("requirements/")):
        return owner
    return None


def workspace_members(ecosystem: str, root: str, read: Reader) -> Tuple[List[str], List[str]]:
    """The ``members`` / ``exclude`` globs a Cargo or uv workspace root declares (``a/*`` form)."""
    manifest, table_path = _WORKSPACE_TABLES[ecosystem]
    prefix = "" if root == "/" else root.strip("/") + "/"
    try:
        table: object = tomllib.loads(read(prefix + manifest) or "")
    except tomllib.TOMLDecodeError:
        return [], []
    for key in table_path:
        table = table.get(key) if isinstance(table, dict) else None
    if not isinstance(table, dict):
        return [], []
    globs = {name: table.get(name) for name in ("members", "exclude")}
    members, exclude = (
        [prefix + str(item).strip("/") for item in value] if isinstance(value, list) else []
        for value in (globs["members"], globs["exclude"])
    )
    return members, exclude


def _in_workspace(ecosystem: str, root: str, directory: str, read: Reader) -> bool:
    """Whether ``directory`` is a declared member (and not excluded) of the workspace at ``root``."""
    members, exclude = workspace_members(ecosystem, root, read)
    target = directory.strip("/")
    return any(fnmatch.fnmatchcase(target, glob) for glob in members) and not any(
        fnmatch.fnmatchcase(target, glob) for glob in exclude
    )


def unscanned_rails(rails: Dict[str, List[str]], paths: Sequence[str], read: Optional[Reader] = None) -> List[str]:
    """Rails whose dependencies gate 6 (osv-scanner) cannot read, so it can never fail on them.

    A lockfile covers the rail of its own directory (pip: also ``requirements/``); in Gradle,
    Maven and NuGet, whose roots fold declared modules into one rail, a module's lockfile
    covers the root rail. A Cargo or uv workspace member shares the root lockfile only when
    the root manifest declares it a member (and does not exclude it). Vendored trees never
    count, and a lockfile in an unrelated subdirectory never hides a rail's blind spot.
    """
    gaps = [f"{label} ({', '.join(rails[eco])})" for eco, label in _UNSCANNED_ECOSYSTEMS.items() if eco in rails]
    for ecosystem, lockfiles in _SCANNED_LOCKFILES.items():
        directories = rails.get(ecosystem, [])
        covered = {
            _lockfile_rail(ecosystem, path, directories)
            for path in paths
            if not _is_excluded(path) and _readable(path, lockfiles)
        }
        for directory in directories:
            shared = (
                read is not None
                and ecosystem in _WORKSPACE_TABLES
                and any(
                    root is not None
                    and _is_ancestor(root, directory)
                    and _in_workspace(ecosystem, root, directory, read)
                    for root in covered
                )
            )
            if directory not in covered and not shared:
                gaps.append(f"{ecosystem} {directory} has no file osv-scanner reads ({' / '.join(lockfiles)})")
    return gaps


def ignores_majors(config_text: str) -> bool:
    """Whether any update entry of a dependabot.yml ignores semver-major updates."""
    try:
        document = yaml.safe_load(config_text)
    except yaml.YAMLError:
        return False
    updates = document.get("updates") if isinstance(document, dict) else None
    for update in updates if isinstance(updates, list) else []:
        ignores = update.get("ignore") if isinstance(update, dict) else None
        for rule in ignores if isinstance(ignores, list) else []:
            types = rule.get("update-types") if isinstance(rule, dict) else None
            if isinstance(types, list) and "version-update:semver-major" in types:
                return True
    return False


def caller_refs(text: str) -> Set[str]:
    """Return the ``reusable-quality.yml`` refs that real jobs (not comments) of a workflow call."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        return set()
    jobs = document.get("jobs") if isinstance(document, dict) else None
    refs: Set[str] = set()
    for job in jobs.values() if isinstance(jobs, dict) else []:
        uses = job.get("uses") if isinstance(job, dict) else None
        if isinstance(uses, str) and uses.startswith(REUSABLE_QUALITY_PREFIX):
            refs.add(uses[len(REUSABLE_QUALITY_PREFIX) :])
    return refs


def caller_contract_gap(text: str, deps_floor: str = "t0") -> str:
    """Why a lean-gate caller would not report ``quality / quality`` on pull requests ('' when it does).

    The required context is ``<caller job name> / <reusable job name>``: the caller job must be
    ``quality`` (id, and ``name`` if it sets one). With ``deps_floor == "any"`` it must also pass
    ``deps-severity-floor: any``, or gate 6 keeps the T0 default the docs no longer describe.
    """
    document = yaml.safe_load(text)
    triggers = document.get("on", document.get(True)) if isinstance(document, dict) else None
    if isinstance(triggers, str):
        triggers = [triggers]
    if not isinstance(triggers, (list, dict)) or "pull_request" not in triggers:
        return "the caller does not run on pull_request, so `quality / quality` never reports on a pull request"
    jobs = document.get("jobs") if isinstance(document, dict) else None
    job = jobs.get("quality") if isinstance(jobs, dict) else None
    if not (
        isinstance(job, dict)
        and str(job.get("uses", "")).startswith(REUSABLE_QUALITY_PREFIX)
        and job.get("name", "quality") == "quality"
    ):
        return "the caller job must be `quality` (id and name), or the required context is not `quality / quality`"
    inputs = job.get("with")
    if deps_floor == "any" and not (isinstance(inputs, dict) and inputs.get("deps-severity-floor") == "any"):
        return "the caller does not pass `deps-severity-floor: any`, so gate 6 still applies the T0 floor"
    return ""


def bump_caller(text: str, qzp_pin: str) -> str:
    """Re-pin every live ``uses:`` line of the lean-gate caller, leaving comments untouched."""
    lines = text.splitlines(keepends=True)
    return "".join(
        line if line.lstrip().startswith("#") else REUSABLE_QUALITY_RE.sub(lambda m: m.group(1) + qzp_pin, line)
        for line in lines
    )


def _yaml_scalar(value: object) -> str:
    """Render one caller input (a string or a number) so YAML reads it back as the same value."""
    if isinstance(value, str) and _PLAIN_SCALAR_RE.fullmatch(value) and yaml.safe_load(value) == value:
        return value
    return json.dumps(value)


def caller_with(inputs: Dict[str, object], deps_floor: str) -> str:
    """Render the caller job's ``with:`` block: the carried-over inputs plus the gate-6 floor.

    ``any`` is always set when asked for; otherwise an existing floor is carried over as is,
    so a regeneration never weakens a caller that already runs at zero tolerance.
    """
    merged = {key: value for key, value in inputs.items() if key != "deps-severity-floor"}
    if deps_floor == "any":
        merged["deps-severity-floor"] = "any"
    elif "deps-severity-floor" in inputs:
        merged["deps-severity-floor"] = inputs["deps-severity-floor"]
    if not merged:
        return ""
    return "    with:\n" + "".join(f"      {key}: {_yaml_scalar(value)}\n" for key, value in merged.items())


def render_triggers(push: Sequence[str], pull_request: Sequence[str], dispatch: bool) -> str:
    """The caller's ``on:`` block. An empty branch list means the event runs unfiltered."""

    def event(name: str, branches: Sequence[str]) -> str:
        if not branches:
            return f"  {name}:\n"
        return f"  {name}:\n    branches: [{', '.join(json.dumps(branch) for branch in branches)}]\n"

    text = event("push", push) + event("pull_request", pull_request)
    text += "  # a merge queue's synthetic commit must report the required check too\n"
    text += "  merge_group:\n    types: [checks_requested]\n"
    return text + ("  workflow_dispatch:\n" if dispatch else "")


@dataclass(frozen=True)
class ThinCaller:
    """What regenerating a thin caller carries over: its inputs and its triggers."""

    inputs: Dict[str, object]
    push: Tuple[str, ...]  # () = unfiltered
    pull_request: Tuple[str, ...]
    dispatch: bool


#: Triggers a thin caller may have; anything else (schedule, workflow_call, ...) is hand-written.
_THIN_TRIGGERS = frozenset({"push", "pull_request", "merge_group", "workflow_dispatch"})


def _trigger_branches(
    triggers: Union[List[str], Dict[str, object]], event: str, default_branch: str
) -> Optional[Tuple[str, ...]]:
    """The branch filter of ``event``: ``()`` unfiltered, the default branch when the event is absent.

    ``None`` when the event carries anything but a non-empty string ``branches`` list (paths,
    tags, branches-ignore, types, ...): only a hand-written caller may do that.
    """
    if not isinstance(triggers, dict):
        return () if event in triggers else (default_branch,)
    if event not in triggers:
        return (default_branch,)
    spec = triggers[event]
    if spec is None:
        return ()
    branches = spec.get("branches") if isinstance(spec, dict) and set(spec) == {"branches"} else None
    if not (isinstance(branches, list) and branches and all(isinstance(b, str) for b in branches)):
        return None
    return tuple(branches)


def thin_caller(text: str, default_branch: str = "main") -> Optional[ThinCaller]:
    """What to carry over when a workflow is a thin lean-gate caller, else ``None``.

    Thin means the managed shape and nothing else: top-level name / on / permissions /
    concurrency / jobs, one ``quality`` job that only ``uses`` the reusable workflow (with
    optional ``with`` inputs, ``name: quality`` and job-level permissions), runs on
    pull_request, and read-only contents permissions at both levels. Its triggers are
    push / pull_request (at most a ``branches`` filter each, which is kept), merge_group and
    an input-less workflow_dispatch. Every input is a string or a number (PyYAML's YAML 1.1
    turns ``on`` or a date into something GitHub reads as text). The baseline owns such a
    caller and regenerates it from the template, carrying all of that over; anything more is
    hand-written and only re-pinned.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(document, dict) or set(document) - _THIN_CALLER_KEYS or caller_contract_gap(text):
        return None
    jobs = document["jobs"]
    job = jobs["quality"]
    inputs = job.get("with") or {}
    if (
        list(jobs) != ["quality"]
        or set(job) - _THIN_CALLER_JOB_KEYS
        or not isinstance(inputs, dict)
        or not all(isinstance(key, str) for key in inputs)
        or not all(isinstance(v, (str, int, float)) and not isinstance(v, bool) for v in inputs.values())
        or document.get("permissions", READ_ONLY) != READ_ONLY
        or job.get("permissions", READ_ONLY) != READ_ONLY
    ):
        return None
    triggers = document.get("on", document.get(True))
    if isinstance(triggers, str):
        triggers = [triggers]
    if isinstance(triggers, list) and not all(isinstance(name, str) for name in triggers):
        return None
    names = set(triggers)
    if names - _THIN_TRIGGERS or (isinstance(triggers, dict) and triggers.get("workflow_dispatch")):
        return None
    push = _trigger_branches(triggers, "push", default_branch)
    pull_request = _trigger_branches(triggers, "pull_request", default_branch)
    if push is None or pull_request is None:
        return None
    return ThinCaller(dict(inputs), push, pull_request, "workflow_dispatch" in names)


def _regenerate_caller(
    workflow: str, text: str, refs: Set[str], thin: ThinCaller, context: Dict[str, str]
) -> FileAction:
    """Regenerate a thin caller from the template, keeping its inputs and triggers."""
    rendered = render_template(
        "quality.yml",
        {
            **context,
            "caller_with": caller_with(thin.inputs, context["deps_floor"]),
            "caller_triggers": render_triggers(thin.push, thin.pull_request, thin.dispatch),
        },
    )
    if rendered == text.replace("\r\n", "\n"):
        return FileAction(workflow, "keep", "lean-gate caller is current")
    reason = f"thin caller regenerated from the fleet template (pin {', '.join(sorted(refs))} -> {context['qzp_pin']}"
    if context["deps_floor"] == "any":
        reason += ", deps-severity-floor: any"
    return FileAction(workflow, "replace", reason + ")", rendered)


def _plan_quality_caller(facts: RepoFacts, read: Reader, context: Dict[str, str], qzp_pin: str) -> List[FileAction]:
    """Create the lean-gate caller, regenerate or re-pin existing callers, or flag an in-repo gate."""
    actions: List[FileAction] = []
    workflows = sorted(
        p for p in facts.paths if posixpath.dirname(p) == ".github/workflows" and p.endswith((".yml", ".yaml"))
    )
    for workflow in workflows:
        text = read(workflow) or ""
        refs = caller_refs(text)
        if not refs:
            continue
        thin = thin_caller(text, facts.default_branch)
        if thin is not None:
            actions.append(_regenerate_caller(workflow, text, refs, thin, context))
            continue
        problem = caller_contract_gap(text, context["deps_floor"])
        if problem:
            actions.append(FileAction(workflow, "gap", problem))
            continue
        if refs == {qzp_pin}:
            actions.append(FileAction(workflow, "keep", "lean gate already pinned to the target commit"))
            continue
        bumped = bump_caller(text, qzp_pin)
        if caller_refs(bumped) != {qzp_pin}:
            actions.append(FileAction(workflow, "gap", f"could not re-pin {', '.join(sorted(refs))} automatically"))
            continue
        actions.append(FileAction(workflow, "bump", f"pin {', '.join(sorted(refs))} -> {qzp_pin}", bumped))
    if actions:
        return actions
    if QUALITY_CALLER in facts.paths:
        return [FileAction(QUALITY_CALLER, "gap", "in-repo quality gate; migrate it to the QZP caller by hand")]
    return [FileAction(QUALITY_CALLER, "create", "no QZP lean-gate caller", render_template("quality.yml", context))]


#: Workflow file names that never test code (scanning, housekeeping, delivery).
_NON_TEST_WORKFLOW_WORDS = (
    "codeql",
    "dependabot",
    "pages",
    "release",
    "labeler",
    "stale",
    "scorecard",
    "deploy",
    "publish",
)


_CPP_TEST_RE = re.compile(
    r"(^|/)([^/]*_tests?\.cpp|[^/]*_test\.(cc|cxx)|test_[^/]*\.cpp|[^/]*Test\.cpp)$|(^|/)tests?/.+\.(cpp|cc)$"
)


def _plan_compiled_lane(facts: RepoFacts) -> List[FileAction]:
    """Flag the prerequisites the lean gate's compiled (Go / C++) coverage lane fails without."""
    languages = detect_languages(facts.paths)
    go_tests = "go" in languages and any(path.endswith("_test.go") for path in facts.paths)
    cpp_tests = "cpp" in languages and any(_CPP_TEST_RE.search(path) for path in facts.paths)
    gaps: List[FileAction] = []
    if go_tests and "go.mod" not in facts.paths:
        gaps.append(FileAction("go.mod", "gap", "Go tests exist but there is no root go.mod; the coverage lane fails"))
    if (go_tests or cpp_tests) and "scripts/coverage-gate.sh" not in facts.paths:
        gaps.append(
            FileAction(
                "scripts/coverage-gate.sh",
                "gap",
                "compiled tests exist but there is no coverage gate script; the coverage lane fails until it measures "
                "100 % line and branch",
            )
        )
    return gaps


#: Build/test tooling that shows a workflow actually exercises a language the lean gate skips.
_LANGUAGE_CI_RE = {
    "c": re.compile(r"\b(make|cmake|ctest|meson|gcc|clang)\b"),
    "java": re.compile(r"\b(gradlew?|mvnw?|maven|setup-java)\b"),
    "kotlin": re.compile(r"\b(gradlew?|mvnw?|maven|setup-java)\b"),
    "csharp": re.compile(r"\b(dotnet|msbuild|setup-dotnet|vstest)\b"),
    "rust": re.compile(r"\b(cargo|rust-toolchain|setup-rust)\b"),
    "swift": re.compile(r"\b(swift|xcodebuild|xcrun)\b"),
}


def _plan_outside_languages(facts: RepoFacts, read: Reader) -> List[FileAction]:
    """Flag languages the lean gate cannot test when no workflow of the repository runs their tooling."""
    outside = [key for key in _OUTSIDE_LEAN_GATE if key in detect_languages(facts.paths)]
    texts = [
        read(path) or ""
        for path in facts.paths
        if posixpath.dirname(path) == ".github/workflows"
        and path.endswith((".yml", ".yaml"))
        and path != QUALITY_CALLER
        and not any(word in posixpath.basename(path).lower() for word in _NON_TEST_WORKFLOW_WORDS)
    ]
    texts = [text for text in texts if not caller_refs(text)]
    untested = [
        _OUTSIDE_LEAN_GATE[key] for key in outside if not any(_LANGUAGE_CI_RE[key].search(text) for text in texts)
    ]
    if not untested:
        return []
    return [
        FileAction(
            ".github/workflows/",
            "gap",
            f"{', '.join(untested)} source has no CI lane: the lean gate does not test it and no workflow of the "
            "repository runs its build or test tooling",
        )
    ]


def _plan_jsts_lane(facts: RepoFacts, read: Reader) -> List[FileAction]:
    """Flag JS/TS tests the lean gate's coverage lane cannot measure (it fails without a coverage script)."""
    if not _has_jsts(detect_languages(facts.paths)):
        return []
    tests = any(
        posixpath.basename(path).split(".")[-2:-1] in (["test"], ["spec"]) or "/__tests__/" in "/" + path
        for path in facts.paths
        if path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")) and not _is_excluded(path)
    )
    if not tests or _coverage_scripts(read, facts.paths) or _test_script_has_coverage(read, facts.paths):
        return []
    return [
        FileAction(
            "package.json",
            "gap",
            "JS/TS tests exist but no package.json has a `test:coverage` script (or a vitest/jest `--coverage` test "
            "script); the coverage lane fails until it measures 100 %",
        )
    ]


def _test_script_has_coverage(read: Reader, paths: Sequence[str]) -> bool:
    """Whether a root or first-level package.json ``test`` script already collects coverage."""
    for path in (p for p in paths if posixpath.basename(p) == "package.json" and p.count("/") <= 1):
        try:
            manifest = json.loads(read(path) or "{}")
        except ValueError:
            continue
        scripts = manifest.get("scripts") if isinstance(manifest, dict) else None
        if isinstance(scripts, dict) and "--coverage" in str(scripts.get("test", "")):
            return True
    return False


def effective_floor(facts: RepoFacts, read: Reader, deps_floor: str) -> str:
    """The gate-6 floor the planned callers enforce: an existing ``any`` is never dropped."""
    if deps_floor == "any":
        return deps_floor
    for path in facts.paths:
        if posixpath.dirname(path) != ".github/workflows" or not path.endswith((".yml", ".yaml")):
            continue
        try:
            document = yaml.safe_load(read(path) or "")
        except yaml.YAMLError:
            continue
        jobs = document.get("jobs") if isinstance(document, dict) else None
        for job in jobs.values() if isinstance(jobs, dict) else []:
            uses = job.get("uses") if isinstance(job, dict) else None
            inputs = job.get("with") if isinstance(job, dict) else None
            if (
                isinstance(uses, str)
                and uses.startswith(REUSABLE_QUALITY_PREFIX)
                and isinstance(inputs, dict)
                and inputs.get("deps-severity-floor") == "any"
            ):
                return "any"
    return deps_floor


def plan_baseline(facts: RepoFacts, read: Reader, qzp_pin: str, deps_floor: str = "t0") -> List[FileAction]:
    """Return every baseline decision for one repository checkout."""
    context = template_context(facts, read, qzp_pin, effective_floor(facts, read, deps_floor))
    caller = _plan_quality_caller(facts, read, context, qzp_pin)
    planned = [action.path for action in caller if action.action == "create"]
    actions = [
        _plan_security(facts, read, context),
        _plan_simple(facts, CONTRIBUTING_LOCATIONS, "CONTRIBUTING.md", "CONTRIBUTING.md", context),
        _plan_simple(facts, CONDUCT_LOCATIONS, "CODE_OF_CONDUCT.md", "CODE_OF_CONDUCT.md", context),
        _plan_simple(facts, PR_TEMPLATE_LOCATIONS, PR_TEMPLATE_LOCATIONS[0], "PULL_REQUEST_TEMPLATE.md", context),
        _plan_simple(facts, AGENTS_LOCATIONS, "AGENTS.md", "AGENTS.md", context),
        _plan_dependabot(facts, read, planned),
    ]
    actions.extend(_plan_issue_forms(facts, context))
    actions.extend(_plan_gate_configs(facts, read, context))
    actions.append(_plan_verify(facts, read, context))
    actions.extend(caller)
    actions.extend(_plan_compiled_lane(facts))
    actions.extend(_plan_jsts_lane(facts, read))
    actions.extend(_plan_outside_languages(facts, read))
    unscanned = unscanned_rails(detect_rails(facts.paths, read), facts.paths, read)
    if unscanned:
        actions.append(
            FileAction(GATE6_SCOPE, "gap", f"gate 6 does not scan these dependencies: {'; '.join(unscanned)}")
        )
    return actions


def apply_actions(repo_dir: Path, actions: Iterable[FileAction]) -> List[str]:
    """Write every create/replace/bump action into the checkout; return the written paths.

    A ``create`` never overwrites: if the target already exists on disk (an untracked or
    ignored file the listing could not see, or a case variant on a case-insensitive file
    system) the run stops with ``FileExistsError`` before anything is lost. No action ever
    writes through a symlink or to a path that resolves outside the checkout.
    """
    pending = [action for action in actions if action.writes and action.content is not None]
    root = repo_dir.resolve()
    for action in pending:
        target = repo_dir / action.path
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise ValueError(f"refusing to write {action.path} through a symlink or outside the checkout")
        if action.action == "create" and target.exists():
            raise FileExistsError(f"refusing to overwrite existing {action.path}")
    written: List[str] = []
    for action in pending:
        target = repo_dir / action.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(action.content or "", encoding="utf-8", newline="\n")
        if action.executable:
            target.chmod(target.stat().st_mode | 0o111)
        written.append(action.path)
    return written


def list_checkout_files(repo_dir: Path, *, runner: ProcessRunner = subprocess.run) -> Tuple[str, ...]:
    """Return tracked plus untracked, non-ignored files that exist on disk.

    Index entries deleted from the worktree are dropped, so a missing baseline file is planned
    again instead of being reported as kept. Raises CalledProcessError when git fails.
    """
    completed = runner(  # nosec S603 — argv array, controlled inputs
        ["git", "-C", str(repo_dir), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    listed = dict.fromkeys(path for path in completed.stdout.split("\0") if path)
    return tuple(path for path in listed if os.path.lexists(repo_dir / path))


def _reader(repo_dir: Path) -> Reader:
    """Return a reader for repo-relative paths (None when the file is absent or unreadable)."""

    def read(relative: str) -> Optional[str]:
        """Read one repo-relative file as UTF-8 text."""
        try:
            return (repo_dir / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    return read


def render_plan(slug: str, actions: Sequence[FileAction]) -> str:
    """Render a human-readable plan."""
    lines = [f"fleet baseline for {slug}:"]
    lines.extend(f"  {action.action:<7} {action.path} — {action.reason}" for action in actions)
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description="Plan, render and audit the fleet governance baseline.")
    parser.add_argument("--repo-dir", required=True, help="Path to the consumer repository checkout.")
    parser.add_argument("--slug", required=True, help="owner/name of the consumer repository.")
    parser.add_argument("--default-branch", default="main")
    parser.add_argument("--private", action="store_true", help="The repository is private (no PVR / CodeQL).")
    parser.add_argument("--upstream", default="", help="owner/name of the parent repository when this is a fork.")
    parser.add_argument("--qzp-pin", required=True, help="40-hex quality-zero-platform commit for the caller.")
    parser.add_argument(
        "--deps-severity-floor",
        choices=DEPS_FLOORS,
        default="t0",
        help="Gate-6 floor for a NEW caller: 'any' (zero tolerance) needs a pin whose reusable-quality.yml "
        "declares the deps-severity-floor input.",
    )
    parser.add_argument("--write", action="store_true", help="Apply create/replace/bump actions to the checkout.")
    parser.add_argument("--json-out", default="", help="Also write the plan as JSON to this path.")
    return parser


def main(argv: Optional[List[str]] = None, *, runner: ProcessRunner = subprocess.run) -> int:
    """Plan (and optionally apply) the baseline; exit 1 while anything is still missing or gapped."""
    args = _build_parser().parse_args(argv)
    if not FULL_SHA_RE.match(args.qzp_pin):
        print(f"ERROR: --qzp-pin must be a full 40-hex commit SHA, got {args.qzp_pin!r}", file=sys.stderr)
        return 2
    repo_dir = Path(args.repo_dir).resolve()
    paths = list_checkout_files(repo_dir, runner=runner)
    facts = RepoFacts(args.slug, args.default_branch, bool(args.private), paths, args.upstream or None)
    actions = plan_baseline(facts, _reader(repo_dir), args.qzp_pin, args.deps_severity_floor)
    print(render_plan(args.slug, actions))
    if args.json_out:
        payload = [{"path": a.path, "action": a.action, "reason": a.reason} for a in actions]
        Path(args.json_out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    if args.write:
        for path in apply_actions(repo_dir, actions):
            print(f"wrote {path}")
        return 1 if any(action.action == "gap" for action in actions) else 0
    return 1 if any(action.writes or action.action == "gap" for action in actions) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
