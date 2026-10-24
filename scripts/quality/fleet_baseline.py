#!/usr/bin/env python3
"""Fleet governance baseline: plan, render and audit what every governed repo carries.

quality-zero-platform is the fleet's governor. ``docs/FLEET-BASELINE.md`` is the
policy; this module is its executable half. For one repository checkout it
decides, file by file, what the baseline requires and what to do about it:

* **community files** — ``SECURITY.md`` (fractal-darkroom's shape: supported
  versions, a private reporting channel with a 72 h / 14 d SLA, scope, supply
  chain), ``CONTRIBUTING.md`` (the gates, the "never weaken a gate" rule, local
  preflight, 100 % line+branch), ``CODE_OF_CONDUCT.md`` (Contributor Covenant
  2.1), a pull-request template whose checklist maps 1:1 to the CI gates, and
  issue forms that route security reports to the private channel;
* **Dependabot** — one update rail per manifest tree for every ecosystem the
  checkout actually contains, plus ``github-actions``. Security updates are
  grouped per ecosystem so ONE pull request can clear every open advisory
  (the dependency gate is zero tolerance, so a stranded advisory reddens every
  other PR); majors are never ignored, they arrive one by one;
* **the lean gate caller** — ``.github/workflows/quality.yml`` calling
  ``reusable-quality.yml`` pinned to an exact 40-hex commit, or a pin bump when
  a caller already exists.

Ownership is conservative. A file the repository wrote itself is never
overwritten: missing files are created, files that are byte-identical to a
known platform-generated boilerplate are upgraded, and everything else is kept
and — for ``dependabot.yml`` — audited for manifest trees that have no rail.
Nothing is committed or pushed here; ``--write`` only edits the checkout.

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

PLATFORM_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_ROOT = PLATFORM_ROOT / "templates" / "fleet"
#: QZP's own curated gate-4 ruleset and gate-4 scope file: the single source the fleet is seeded from.
OPENGREP_SOURCE = PLATFORM_ROOT / ".quality" / "opengrep"
SEMGREPIGNORE_SOURCE = PLATFORM_ROOT / "docs" / "lean-gate" / "semgrepignore.template"
MAINTAINER_URL = "https://github.com/Prekzursil"
REUSABLE_QUALITY_RE = re.compile(
    r"(uses:\s*Prekzursil/quality-zero-platform/\.github/workflows/reusable-quality\.yml@)([^\s#'\"]+)"
)
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

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
}
#: Ecosystems whose root manifest already covers nested modules (multi-project builds).
_TOPMOST_ONLY = frozenset({"gradle", "maven", "nuget"})
#: Ecosystems updated at the repository root regardless of where the marker file sits.
_ROOT_RAILS = frozenset({"gitsubmodule", "devcontainers"})
#: Ecosystems without semantic versions (update-types would be meaningless).
_NO_SEMVER = frozenset({"gitsubmodule"})
#: (winner, loser): a lockfile that selects a dedicated ecosystem for its directory.
_SUPERSEDES = (("bun", "npm"), ("uv", "pip"))

LANGUAGE_EXTENSIONS: Dict[str, Tuple[str, ...]] = {
    "python": (".py",),
    "ts": (".ts", ".tsx"),
    "js": (".js", ".jsx", ".mjs", ".cjs"),
    "go": (".go",),
    "cpp": (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"),
    "java": (".java",),
    "kotlin": (".kt",),
    "csharp": (".cs",),
    "rust": (".rs",),
    "swift": (".swift",),
}
#: Languages the lean gate does not measure; their repos must test them in their own CI.
_OUTSIDE_LEAN_GATE = {"java": "Java", "kotlin": "Kotlin", "csharp": "C#", "rust": "Rust", "swift": "Swift"}

SECURITY_LOCATIONS = ("SECURITY.md", ".github/SECURITY.md", "docs/SECURITY.md")
CONTRIBUTING_LOCATIONS = ("CONTRIBUTING.md", ".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md")
CONDUCT_LOCATIONS = ("CODE_OF_CONDUCT.md", ".github/CODE_OF_CONDUCT.md", "docs/CODE_OF_CONDUCT.md")
PR_TEMPLATE_LOCATIONS = (
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/pull_request_template.md",
    "PULL_REQUEST_TEMPLATE.md",
    "pull_request_template.md",
    "docs/PULL_REQUEST_TEMPLATE.md",
    "docs/pull_request_template.md",
)
DEPENDABOT_LOCATIONS = (".github/dependabot.yml", ".github/dependabot.yaml")
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


def _ecosystem_for(path: str) -> Optional[str]:
    """Return the Dependabot ecosystem a manifest path belongs to, if any."""
    name = posixpath.basename(path)
    parent = posixpath.dirname(path)
    if (parent == ".github/workflows" and name.endswith((".yml", ".yaml"))) or name in ("action.yml", "action.yaml"):
        return "github-actions"
    if name in _PIP_NAMES or (name.startswith("requirements") and name.endswith((".txt", ".in"))):
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
    return "/" + parent if parent else "/"


def _is_ancestor(ancestor: str, directory: str) -> bool:
    """Whether ``ancestor`` strictly contains ``directory`` (Dependabot directory form)."""
    if ancestor == directory:
        return False
    return ancestor == "/" or directory.startswith(ancestor.rstrip("/") + "/")


def _topmost(directories: Set[str]) -> Set[str]:
    """Keep only directories that no other directory in the set contains."""
    return {d for d in directories if not any(_is_ancestor(other, d) for other in directories)}


def _dir_sort_key(directory: str) -> Tuple[bool, str]:
    """Sort the repository root first, then alphabetically."""
    return (directory != "/", directory)


def detect_rails(paths: Iterable[str]) -> Dict[str, List[str]]:
    """Map every Dependabot ecosystem present in ``paths`` to its manifest directories."""
    found: Dict[str, Set[str]] = {}
    for raw in paths:
        path = _clean(raw)
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
        directories = _topmost(found[ecosystem]) if ecosystem in _TOPMOST_ONLY else found[ecosystem]
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
    honored (``fnmatch`` semantics, slightly more permissive than Dependabot's).
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
        directories = update.get("directories") or [update.get("directory")]
        covered.setdefault(str(update.get("package-ecosystem", "")), []).extend(
            _normalize_dir(str(item)) for item in directories if item
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


def gate_rows(languages: Sequence[str]) -> List[Tuple[str, str, str]]:
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
    none = "n/a — no such source in this repository"
    return [
        ("1 · lint + format", "; ".join(lint) or none, "zero findings and zero format diffs"),
        ("2 · types", "; ".join(types) or none, "zero errors **and** zero warnings"),
        ("3 · tests + coverage", "; ".join(tests) or none, "every test passes at 100 % line **and** branch"),
        ("4 · SAST", "opengrep with the curated `.quality/opengrep` ruleset (seeded from QZP)", "zero findings"),
        ("5 · secrets", "gitleaks", "zero findings"),
        (
            "6 · dependencies",
            "osv-scanner on every lockfile and manifest (`osv-scanner.toml`)",
            "zero known advisories, any severity",
        ),
    ]


def _python_uses_pytest(read: Callable[[str], Optional[str]], paths: Sequence[str]) -> bool:
    """Whether the Python suite runs under pytest (else unittest)."""
    if "pytest.ini" in paths or "conftest.py" in paths or any(p.endswith("/conftest.py") for p in paths):
        return True
    for candidate in ("pyproject.toml", "setup.cfg", "tox.ini", "requirements-dev.txt", "requirements.txt"):
        text = read(candidate)
        if text and "pytest" in text:
            return True
    return False


def _coverage_scripts(read: Callable[[str], Optional[str]], paths: Sequence[str]) -> List[str]:
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


def local_commands(languages: Sequence[str], read: Callable[[str], Optional[str]], paths: Sequence[str]) -> str:
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


def template_context(facts: RepoFacts, read: Callable[[str], Optional[str]], qzp_pin: str) -> Dict[str, str]:
    """Build the substitution context every fleet template renders against."""
    languages = detect_languages(facts.paths)
    rows = gate_rows(languages)
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
    return {
        "repo_slug": facts.slug,
        "display_name": facts.name,
        "default_branch": facts.default_branch,
        "reporting_channel": reporting,
        "security_controls": "\n".join(controls),
        "security_contact_url": report_url,
        "conduct_contact": conduct,
        "gate_rows": "\n".join(f"| {gate} | {what} | {passes} |" for gate, what, passes in rows),
        "gate_checklist": "\n".join(checklist),
        "extra_controls": "\n".join(extra),
        "local_commands": local_commands(languages, read, facts.paths),
        "qzp_pin": qzp_pin,
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


def _first_present(paths: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    """Return the first candidate path present in the checkout."""
    present = set(paths)
    return next((candidate for candidate in candidates if candidate in present), None)


def _plan_security(facts: RepoFacts, read: Callable[[str], Optional[str]], context: Dict[str, str]) -> FileAction:
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
    facts: RepoFacts, candidates: Sequence[str], target: str, template: str, context: Dict[str, str]
) -> FileAction:
    """Create ``target`` from ``template`` unless any candidate location already exists."""
    existing = _first_present(facts.paths, candidates)
    if existing is not None:
        return FileAction(existing, "keep", "repository-authored file")
    return FileAction(target, "create", "missing", render_template(template, context))


def _plan_issue_forms(facts: RepoFacts, context: Dict[str, str]) -> List[FileAction]:
    """Create the issue forms unless the repository already ships an ISSUE_TEMPLATE directory."""
    if any(path.startswith(ISSUE_TEMPLATE_DIR) for path in facts.paths):
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
    if SEMGREPIGNORE in facts.paths:
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
        actions.append(FileAction(OSV_CONFIG, "keep", "repository osv-scanner config"))
    else:
        actions.append(FileAction(OSV_CONFIG, "create", "gate 6 has no config", render_template(OSV_CONFIG, context)))
    return actions


def _plan_dependabot(facts: RepoFacts, read: Callable[[str], Optional[str]], planned: Sequence[str] = ()) -> FileAction:
    """Create dependabot.yml, or audit an existing one for manifest trees without a rail.

    ``planned`` are paths this run is about to create (the lean-gate caller), so a
    repository that gains its first workflow also gains its ``github-actions`` rail.
    """
    rails = detect_rails(list(facts.paths) + list(planned))
    existing = _first_present(facts.paths, DEPENDABOT_LOCATIONS)
    if existing is None:
        if not rails:
            return FileAction(DEPENDABOT_LOCATIONS[0], "keep", "no manifests or workflows to update")
        return FileAction(DEPENDABOT_LOCATIONS[0], "create", "missing", render_dependabot(rails))
    missing = uncovered_rails(rails, read(existing) or "")
    if not missing:
        return FileAction(existing, "keep", "every manifest tree has a rail")
    listed = ", ".join(f"{ecosystem} {directory}" for ecosystem, directory in missing)
    return FileAction(existing, "gap", f"manifest trees without a Dependabot rail: {listed}")


def _plan_quality_caller(
    facts: RepoFacts, read: Callable[[str], Optional[str]], context: Dict[str, str], qzp_pin: str
) -> List[FileAction]:
    """Create the lean-gate caller, bump existing callers to ``qzp_pin``, or flag an in-repo gate."""
    actions: List[FileAction] = []
    workflows = sorted(
        p for p in facts.paths if posixpath.dirname(p) == ".github/workflows" and p.endswith((".yml", ".yaml"))
    )
    for workflow in workflows:
        text = read(workflow) or ""
        refs = {match.group(2) for match in REUSABLE_QUALITY_RE.finditer(text)}
        if not refs:
            continue
        if refs == {qzp_pin}:
            actions.append(FileAction(workflow, "keep", "lean gate already pinned to the target commit"))
            continue
        bumped = REUSABLE_QUALITY_RE.sub(lambda match: match.group(1) + qzp_pin, text)
        actions.append(FileAction(workflow, "bump", f"pin {', '.join(sorted(refs))} -> {qzp_pin}", bumped))
    if actions:
        return actions
    if QUALITY_CALLER in facts.paths:
        return [FileAction(QUALITY_CALLER, "gap", "in-repo quality gate; migrate it to the QZP caller by hand")]
    return [FileAction(QUALITY_CALLER, "create", "no QZP lean-gate caller", render_template("quality.yml", context))]


def plan_baseline(facts: RepoFacts, read: Callable[[str], Optional[str]], qzp_pin: str) -> List[FileAction]:
    """Return every baseline decision for one repository checkout."""
    context = template_context(facts, read, qzp_pin)
    caller = _plan_quality_caller(facts, read, context, qzp_pin)
    planned = [action.path for action in caller if action.action == "create"]
    actions = [
        _plan_security(facts, read, context),
        _plan_simple(facts, CONTRIBUTING_LOCATIONS, "CONTRIBUTING.md", "CONTRIBUTING.md", context),
        _plan_simple(facts, CONDUCT_LOCATIONS, "CODE_OF_CONDUCT.md", "CODE_OF_CONDUCT.md", context),
        _plan_simple(facts, PR_TEMPLATE_LOCATIONS, PR_TEMPLATE_LOCATIONS[0], "PULL_REQUEST_TEMPLATE.md", context),
        _plan_dependabot(facts, read, planned),
    ]
    actions.extend(_plan_issue_forms(facts, context))
    actions.extend(_plan_gate_configs(facts, context))
    actions.extend(caller)
    return actions


def apply_actions(repo_dir: Path, actions: Iterable[FileAction]) -> List[str]:
    """Write every create/replace/bump action into the checkout; return the written paths."""
    written: List[str] = []
    for action in actions:
        if not action.writes or action.content is None:
            continue
        target = repo_dir / action.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(action.content, encoding="utf-8", newline="\n")
        written.append(action.path)
    return written


def list_tracked_files(repo_dir: Path, *, runner: ProcessRunner = subprocess.run) -> Tuple[str, ...]:
    """Return ``git ls-files`` for the checkout (raises CalledProcessError on failure)."""
    completed = runner(  # nosec S603 — argv array, controlled inputs
        ["git", "-C", str(repo_dir), "ls-files", "-z"], capture_output=True, text=True, check=True
    )
    return tuple(path for path in completed.stdout.split("\0") if path)


def _reader(repo_dir: Path) -> Callable[[str], Optional[str]]:
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
    facts = RepoFacts(args.slug, args.default_branch, bool(args.private), list_tracked_files(repo_dir, runner=runner))
    actions = plan_baseline(facts, _reader(repo_dir), args.qzp_pin)
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
