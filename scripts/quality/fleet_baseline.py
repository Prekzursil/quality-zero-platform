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
reported as a gap. Nothing is committed or pushed here; ``--write`` only edits
the checkout, and it refuses to create a file that already exists on disk.

Usage, from the platform repo root::

    python -m scripts.quality.fleet_baseline --repo-dir ../consumer \\
        --slug Prekzursil/<name> --qzp-pin <40-hex sha> [--private] [--write]
"""

from __future__ import absolute_import

import argparse
import fnmatch
import hashlib
import json
import posixpath
import re
import string
import subprocess  # nosec B404 — git wrapper; all args are controlled
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

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
    r"(uses:\s*Prekzursil/quality-zero-platform/\.github/workflows/reusable-quality\.yml@)([^\s#'\"]+)"
)
REUSABLE_QUALITY_PREFIX = "Prekzursil/quality-zero-platform/.github/workflows/reusable-quality.yml@"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DEPS_FLOORS = ("t0", "any")

#: Path components whose manifests are vendored, generated or test data — never a rail.
EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "vendor",
        "third_party",
        "dist",
        "build",
        "out",
        "__pycache__",
        "site-packages",
        ".tox",
        "fixtures",
        "__fixtures__",
        "testdata",
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

SECURITY_LOCATIONS = ("SECURITY.md", ".github/SECURITY.md", "docs/SECURITY.md")
CONTRIBUTING_LOCATIONS = ("CONTRIBUTING.md", ".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md")
CONDUCT_LOCATIONS = ("CODE_OF_CONDUCT.md", ".github/CODE_OF_CONDUCT.md", "docs/CODE_OF_CONDUCT.md")
PR_TEMPLATE_LOCATIONS = (
    ".github/PULL_REQUEST_TEMPLATE.md",
    "PULL_REQUEST_TEMPLATE.md",
    "docs/PULL_REQUEST_TEMPLATE.md",
)
DEPENDABOT_LOCATIONS = (".github/dependabot.yml", ".github/dependabot.yaml")
AGENTS_LOCATIONS = ("AGENTS.md",)
VERIFY_SCRIPT = "scripts/verify"
ISSUE_TEMPLATE_DIR = ".github/ISSUE_TEMPLATE/"
ISSUE_FORMS = ("config.yml", "bug_report.yml", "feature_request.yml")
QUALITY_CALLER = ".github/workflows/quality.yml"
#: The lean gate runs gate 4 only when this directory exists and gate 6 only when OSV_CONFIG
#: exists, so the baseline seeds both; without them a "zero tolerance" caller silently skips.
OPENGREP_DIR = ".quality/opengrep/"
SEMGREPIGNORE = ".semgrepignore"
OSV_CONFIG = "osv-scanner.toml"
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


def _declares(text: str, relative: str) -> bool:
    """Whether a build file's text names ``relative`` as a module (path, gradle or .sln spelling)."""
    for spelling in {relative, relative.replace("/", ":"), relative.replace("/", "\\")}:
        if re.search(r"(?<![\w.-])" + re.escape(spelling) + r"(?![\w.-])", text):
            return True
    return False


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
            if _declares(_build_text(ecosystem, ancestor, paths, read), relative):
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
        extension = posixpath.splitext(path)[1].lower()
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


def _python_uses_pytest(read: Reader, paths: Sequence[str]) -> bool:
    """Whether the Python suite runs under pytest (else unittest)."""
    if "pytest.ini" in paths or "conftest.py" in paths or any(p.endswith("/conftest.py") for p in paths):
        return True
    for candidate in ("pyproject.toml", "setup.cfg", "tox.ini", "requirements-dev.txt", "requirements.txt"):
        text = read(candidate)
        if text and "pytest" in text:
            return True
    return False


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
        if _python_uses_pytest(read, paths):
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
    """The private reporting URL: PVR for public repos, the maintainer for private ones."""
    if facts.private:
        return MAINTAINER_URL
    return f"https://github.com/{facts.slug}/security/advisories/new"


def _first_present(paths: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    """Return the checkout's spelling of the first candidate present, matched case-insensitively.

    GitHub recognises community files case-insensitively and macOS / Windows checkouts are
    case-insensitive too, so ``security.md`` must count as an existing ``SECURITY.md``.
    """
    present: Dict[str, str] = {}
    for path in paths:
        present.setdefault(path.lower(), path)
    return next((present[c.lower()] for c in candidates if c.lower() in present), None)


def template_context(facts: RepoFacts, read: Reader, qzp_pin: str, deps_floor: str = "t0") -> Dict[str, str]:
    """Build the substitution context every fleet template renders against."""
    languages = detect_languages(facts.paths)
    rows = gate_rows(languages, deps_floor)
    report_url = _private_reporting(facts)
    if facts.private:
        reporting = (
            "This repository is private. Report privately to the maintainer through their "
            f"[GitHub profile]({MAINTAINER_URL}) and keep the details out of issues and pull requests."
        )
        codeql = (
            "- **Code scanning** — CodeQL needs GitHub Advanced Security on private repositories; until it is "
            "available the opengrep SAST gate is the code-scanning control here."
        )
        conduct = (
            f"Report conduct violations privately to the maintainer through their [GitHub profile]({MAINTAINER_URL})."
        )
    else:
        reporting = (
            f"**Please use [private vulnerability reporting]({report_url})** — the *Report a vulnerability* "
            "button in this repository's *Security* tab. Reports there go privately to the maintainer and allow "
            "coordinated disclosure."
        )
        codeql = (
            "- **CodeQL** — GitHub default setup with the *extended* query suite and the remote + local threat "
            "model, on every pull request and weekly; a pull request that introduces a code-scanning alert of any "
            "severity cannot be merged."
        )
        conduct = (
            f"Report conduct violations privately via [private vulnerability reporting]({report_url}) (a private "
            f"channel to the maintainer) or through the maintainer's [GitHub profile]({MAINTAINER_URL})."
        )
    secrets = "- **Secrets** — gitleaks on every change" + (
        "." if facts.private else "; GitHub secret scanning with push protection is enabled."
    )
    dependencies = (
        "- **Dependencies** — osv-scanner on every lockfile and manifest, plus Dependabot alerts and grouped "
        "security updates."
    )
    controls = [codeql, "- **SAST** — opengrep on every change.", secrets, dependencies]
    outside = [label for key, label in _OUTSIDE_LEAN_GATE.items() if key in languages]
    extra = [codeql, "- **Dependabot** — `.github/dependabot.yml` keeps every manifest tree on a weekly update rail."]
    if outside:
        extra.append(
            f"- **{', '.join(outside)}** — not measured by the lean gate; this repository's own CI workflow(s) "
            "test it and must be green too."
        )
    checklist = [f"- [ ] **gate {gate}** — {what}: {passes}" for gate, what, passes in rows]
    caller_with = "    with:\n      deps-severity-floor: any\n" if deps_floor == "any" else ""
    return {
        "repo_slug": facts.slug,
        "display_name": facts.name,
        "default_branch": facts.default_branch,
        "reporting_channel": reporting,
        "security_controls": "\n".join(controls),
        "security_contact_url": report_url,
        "security_policy_path": _first_present(facts.paths, SECURITY_LOCATIONS) or "SECURITY.md",
        "conduct_contact": conduct,
        "gate_rows": "\n".join(f"| {gate} | {what} | {passes} |" for gate, what, passes in rows),
        "gate_checklist": "\n".join(checklist),
        "extra_controls": "\n".join(extra),
        "local_commands": local_commands(languages, read, facts.paths),
        "qzp_pin": qzp_pin,
        "caller_with": caller_with,
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


def _plan_issue_forms(facts: RepoFacts, context: Dict[str, str]) -> List[FileAction]:
    """Create the issue forms unless the repository already ships an ISSUE_TEMPLATE directory."""
    if any(path.lower().startswith(ISSUE_TEMPLATE_DIR.lower()) for path in facts.paths):
        return [FileAction(ISSUE_TEMPLATE_DIR, "keep", "repository-authored issue templates")]
    return [
        FileAction(ISSUE_TEMPLATE_DIR + form, "create", "missing", render_template("ISSUE_TEMPLATE/" + form, context))
        for form in ISSUE_FORMS
    ]


def _plan_gate_configs(facts: RepoFacts, context: Dict[str, str]) -> List[FileAction]:
    """Seed the gate-4 ruleset + scope file and the gate-6 config so neither gate silently skips."""
    languages = set(detect_languages(facts.paths))
    actions: List[FileAction] = []
    if any(path.startswith(OPENGREP_DIR) for path in facts.paths):
        actions.append(FileAction(OPENGREP_DIR, "keep", "repository-curated SAST ruleset"))
    else:
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
                "gate 4 has no ruleset",
                (OPENGREP_SOURCE / name).read_text(encoding="utf-8"),
            )
            for name, needs in OPENGREP_RULES
            if not needs or languages.intersection(needs)
        )
    scope = _first_present(facts.paths, (SEMGREPIGNORE,))
    if scope is not None:
        actions.append(FileAction(scope, "keep", "repository-chosen SAST scope"))
    else:
        actions.append(
            FileAction(
                SEMGREPIGNORE,
                "create",
                "without it opengrep silently skips test trees",
                SEMGREPIGNORE_SOURCE.read_text(encoding="utf-8"),
            )
        )
    osv = _first_present(facts.paths, (OSV_CONFIG,))
    if osv is not None:
        actions.append(FileAction(osv, "keep", "repository osv-scanner config"))
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
    missing = uncovered_rails(rails, text)
    if not missing:
        return FileAction(existing, "keep", "every manifest tree has a rail")
    listed = ", ".join(f"{ecosystem} {directory}" for ecosystem, directory in missing)
    return FileAction(existing, "gap", f"manifest trees without a Dependabot rail: {listed}")


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


def bump_caller(text: str, qzp_pin: str) -> str:
    """Re-pin every live ``uses:`` line of the lean-gate caller, leaving comments untouched."""
    lines = text.splitlines(keepends=True)
    return "".join(
        line if line.lstrip().startswith("#") else REUSABLE_QUALITY_RE.sub(lambda m: m.group(1) + qzp_pin, line)
        for line in lines
    )


def _plan_quality_caller(facts: RepoFacts, read: Reader, context: Dict[str, str], qzp_pin: str) -> List[FileAction]:
    """Create the lean-gate caller, bump existing callers to ``qzp_pin``, or flag an in-repo gate."""
    actions: List[FileAction] = []
    workflows = sorted(
        p for p in facts.paths if posixpath.dirname(p) == ".github/workflows" and p.endswith((".yml", ".yaml"))
    )
    for workflow in workflows:
        text = read(workflow) or ""
        refs = caller_refs(text)
        if not refs:
            continue
        if refs == {qzp_pin}:
            actions.append(FileAction(workflow, "keep", "lean gate already pinned to the target commit"))
            continue
        actions.append(
            FileAction(workflow, "bump", f"pin {', '.join(sorted(refs))} -> {qzp_pin}", bump_caller(text, qzp_pin))
        )
    if actions:
        return actions
    if QUALITY_CALLER in facts.paths:
        return [FileAction(QUALITY_CALLER, "gap", "in-repo quality gate; migrate it to the QZP caller by hand")]
    return [FileAction(QUALITY_CALLER, "create", "no QZP lean-gate caller", render_template("quality.yml", context))]


def _plan_outside_languages(facts: RepoFacts) -> List[FileAction]:
    """Flag languages the lean gate cannot test when the repository has no CI of its own for them."""
    outside = [label for key, label in _OUTSIDE_LEAN_GATE.items() if key in detect_languages(facts.paths)]
    own_ci = [
        path
        for path in facts.paths
        if posixpath.dirname(path) == ".github/workflows"
        and path.endswith((".yml", ".yaml"))
        and path != QUALITY_CALLER
    ]
    if not outside or own_ci:
        return []
    return [
        FileAction(
            ".github/workflows/",
            "gap",
            f"{', '.join(outside)} source has no CI lane: the lean gate does not test it and the repository "
            "has no workflow of its own",
        )
    ]


def plan_baseline(facts: RepoFacts, read: Reader, qzp_pin: str, deps_floor: str = "t0") -> List[FileAction]:
    """Return every baseline decision for one repository checkout."""
    context = template_context(facts, read, qzp_pin, deps_floor)
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
    actions.extend(_plan_gate_configs(facts, context))
    actions.append(_plan_simple(facts, (VERIFY_SCRIPT,), VERIFY_SCRIPT, "verify", context, executable=True))
    actions.extend(caller)
    actions.extend(_plan_outside_languages(facts))
    return actions


def apply_actions(repo_dir: Path, actions: Iterable[FileAction]) -> List[str]:
    """Write every create/replace/bump action into the checkout; return the written paths.

    A ``create`` never overwrites: if the target already exists on disk (an untracked or
    ignored file the listing could not see, or a case variant on a case-insensitive file
    system) the run stops with ``FileExistsError`` before anything is lost.
    """
    pending = [action for action in actions if action.writes and action.content is not None]
    for action in pending:
        if action.action == "create" and (repo_dir / action.path).exists():
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
    """Return tracked plus untracked, non-ignored files (raises CalledProcessError on failure)."""
    completed = runner(  # nosec S603 — argv array, controlled inputs
        ["git", "-C", str(repo_dir), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    )
    return tuple(dict.fromkeys(path for path in completed.stdout.split("\0") if path))


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
    facts = RepoFacts(args.slug, args.default_branch, bool(args.private), paths)
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
