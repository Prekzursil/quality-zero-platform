"""Coverage for ``scripts/quality/fleet_baseline.py`` (the fleet governor baseline)."""

from __future__ import absolute_import

import datetime
import io
import json
import os
import stat
import subprocess
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Dict, List, Optional

import yaml  # type: ignore[import-untyped]

from scripts.quality import fleet_baseline as fb
from scripts.quality.render_repo_baseline import render_security_policy

PIN = "a" * 40
CALLER_PREFIX = "Prekzursil/quality-zero-platform/.github/workflows/reusable-quality.yml@"


def _reader(files: Dict[str, str]):
    """Return a read callable over an in-memory checkout."""

    def read(relative: str) -> Optional[str]:
        """Read one in-memory file."""
        return files.get(relative)

    return read


def _facts(paths: List[str], *, private: bool = False, slug: str = "Prekzursil/demo") -> fb.RepoFacts:
    """Build facts for an in-memory checkout."""
    return fb.RepoFacts(slug, "main", private, tuple(paths))


def _by_path(actions: List[fb.FileAction]) -> Dict[str, fb.FileAction]:
    """Index actions by path."""
    return {action.path: action for action in actions}


class PathHelperTests(unittest.TestCase):
    """Path normalisation, exclusion and case-insensitive discovery."""

    def test_clean_strips_dot_slash_and_backslashes(self) -> None:
        self.assertEqual(fb._clean("./a/b.txt"), "a/b.txt")
        self.assertEqual(fb._clean(" a\\b.txt "), "a/b.txt")
        self.assertEqual(fb._clean("a/b.txt"), "a/b.txt")

    def test_excluded_only_checks_directories(self) -> None:
        self.assertTrue(fb._is_excluded("web/node_modules/x/package.json"))
        self.assertFalse(fb._is_excluded("web/package.json"))
        self.assertFalse(fb._is_excluded("vendor"))

    def test_repo_facts_name(self) -> None:
        self.assertEqual(_facts([]).name, "demo")

    def test_first_present_is_case_insensitive_and_keeps_the_checkout_spelling(self) -> None:
        paths = ["docs/Security.md", "security.md", "SECURITY.MD"]
        self.assertEqual(fb._first_present(paths, fb.SECURITY_LOCATIONS), "security.md")
        self.assertEqual(fb._first_present(["docs/Security.md"], fb.SECURITY_LOCATIONS), "docs/Security.md")
        self.assertIsNone(fb._first_present(["README.md"], fb.SECURITY_LOCATIONS))


class EcosystemTests(unittest.TestCase):
    """Manifest -> Dependabot ecosystem mapping."""

    def test_every_ecosystem(self) -> None:
        cases = {
            ".github/workflows/ci.yml": "github-actions",
            ".github/workflows/ci.yaml": "github-actions",
            "actions/setup/action.yml": "github-actions",
            "action.yaml": "github-actions",
            "pyproject.toml": "pip",
            "requirements-dev.txt": "pip",
            "requirements.in": "pip",
            "requirements/base.txt": "pip",
            "svc/requirements/dev.in": "pip",
            "build.gradle.kts": "gradle",
            "src/App.csproj": "nuget",
            "packages.config": "nuget",
            "compose.yaml": "docker-compose",
            "Dockerfile": "docker",
            "Containerfile": "docker",
            "Dockerfile.dev": "docker",
            "api.Dockerfile": "docker",
            "infra/main.tf": "terraform",
            ".gitmodules": "gitsubmodule",
            ".devcontainer/devcontainer.json": "devcontainers",
            ".devcontainer.json": "devcontainers",
            "web/package.json": "npm",
            "go.mod": "gomod",
            "pom.xml": "maven",
            "charts/app/Chart.yaml": "helm",
        }
        for path, ecosystem in cases.items():
            with self.subTest(path=path):
                self.assertEqual(fb._ecosystem_for(path), ecosystem)

    def test_non_manifests(self) -> None:
        for path in (
            ".github/workflows/README.md",
            "requirements.lock",
            "requirements/README.md",
            "README.md",
            "sub/.gitmodules",
        ):
            with self.subTest(path=path):
                self.assertIsNone(fb._ecosystem_for(path))

    def test_rail_dir(self) -> None:
        self.assertEqual(fb._rail_dir("github-actions", ".github/workflows/ci.yml"), "/")
        self.assertEqual(fb._rail_dir("github-actions", "actions/setup/action.yml"), "/actions/setup")
        self.assertEqual(fb._rail_dir("github-actions", "action.yml"), "/")
        self.assertEqual(fb._rail_dir("devcontainers", ".devcontainer/devcontainer.json"), "/")
        self.assertEqual(fb._rail_dir("npm", "web/package.json"), "/web")
        self.assertEqual(fb._rail_dir("pip", "requirements/base.txt"), "/")
        self.assertEqual(fb._rail_dir("pip", "svc/requirements/dev.in"), "/svc")

    def test_ancestry(self) -> None:
        self.assertFalse(fb._is_ancestor("/a", "/a"))
        self.assertTrue(fb._is_ancestor("/", "/a"))
        self.assertTrue(fb._is_ancestor("/a", "/a/b"))
        self.assertFalse(fb._is_ancestor("/a", "/ab"))


class DetectRailsTests(unittest.TestCase):
    """Rail detection over a whole checkout."""

    def test_detects_supersedes_and_keeps_nested_builds_without_a_reader(self) -> None:
        paths = [
            "",
            ".github/workflows/ci.yml",
            "package.json",
            "bun.lock",
            "web/package.json",
            "node_modules/left-pad/package.json",
            "api/pyproject.toml",
            "api/uv.lock",
            "tools/requirements.txt",
            "build.gradle",
            "app/build.gradle",
            "README.md",
        ]
        self.assertEqual(
            fb.detect_rails(paths),
            {
                "bun": ["/"],
                "github-actions": ["/"],
                "gradle": ["/", "/app"],
                "npm": ["/web"],
                "pip": ["/tools"],
                "uv": ["/api"],
            },
        )

    def test_nested_builds_collapse_only_when_declared(self) -> None:
        files = {
            "settings.gradle.kts": 'rootProject.name = "x"\ninclude(":app", ":libs:core")\n',
            "pom.xml": "<modules><module>svc</module></modules>",
            "App.sln": 'Project("{FAE}") = "Lib", "lib\\Lib.csproj", "{1}"\n',
        }
        paths = list(files) + [
            "build.gradle.kts",
            "app/build.gradle.kts",
            "libs/core/build.gradle.kts",
            "tools/build.gradle.kts",
            "application/build.gradle.kts",
            "svc/pom.xml",
            "standalone/pom.xml",
            "lib/Lib.csproj",
            "other/Other.csproj",
            "nested/App.sln",
            "package.json",
            "web/package.json",
        ]
        rails = fb.detect_rails(paths, _reader(files))
        self.assertEqual(rails["gradle"], ["/", "/application", "/tools"])
        self.assertEqual(rails["maven"], ["/", "/standalone"])
        self.assertEqual(rails["nuget"], ["/", "/nested", "/other"])
        self.assertEqual(rails["npm"], ["/", "/web"])

    def test_commented_declarations_do_not_collapse(self) -> None:
        files = {
            "settings.gradle": '// include(":tools")\n/* include(":old") */\ninclude(":app")\n',
            "pom.xml": "<!-- <module>legacy</module> -->",
        }
        paths = list(files) + [
            "tools/build.gradle",
            "old/build.gradle",
            "app/build.gradle",
            "legacy/pom.xml",
        ]
        rails = fb.detect_rails(paths, _reader(files))
        self.assertEqual(rails["gradle"], ["/", "/old", "/tools"])
        self.assertEqual(rails["maven"], ["/", "/legacy"])

    def test_declared_modules_follow_each_ecosystems_syntax(self) -> None:
        pom = "<artifactId>tools</artifactId><modules><module>svc</module><module> ./web/ </module></modules>"
        self.assertEqual(fb.declared_modules("maven", pom), {"svc", "web"})
        gradle = 'include(":app", ":libs:core")\ninclude \'extra\'\nincludeBuild("tools")\n// include(":old")\n'
        self.assertEqual(fb.declared_modules("gradle", gradle), {"app", "libs/core", "extra"})
        sln = 'Project("{FAE}") = "Lib", "src\\lib\\Lib.csproj", "{1}"\nEndProject\n'
        self.assertEqual(fb.declared_modules("nuget", sln), {"src/lib"})
        self.assertEqual(fb.declared_modules("maven", ""), set())

    def test_superseded_ecosystem_disappears_entirely(self) -> None:
        self.assertEqual(fb.detect_rails(["package.json", "bun.lockb"]), {"bun": ["/"]})

    def test_empty_checkout(self) -> None:
        self.assertEqual(fb.detect_rails([]), {})


class RenderDependabotTests(unittest.TestCase):
    """The zero-tolerance Dependabot config."""

    def test_render_shapes(self) -> None:
        text = fb.render_dependabot({"npm": ["/", "/web"], "gitsubmodule": ["/"], "github-actions": ["/"]})
        self.assertTrue(text.startswith("# Managed by quality-zero-platform"))
        document = yaml.safe_load(text)
        self.assertEqual(document["version"], 2)
        npm, submodule, actions = document["updates"]
        self.assertEqual(npm["directories"], ["/", "/web"])
        self.assertNotIn("directory", npm)
        self.assertEqual(npm["groups"]["npm-security"], {"applies-to": "security-updates", "patterns": ["*"]})
        self.assertEqual(npm["groups"]["npm-minor-patch"]["update-types"], ["minor", "patch"])
        self.assertNotIn("ignore", npm)
        self.assertEqual(submodule["directory"], "/")
        self.assertNotIn("update-types", submodule["groups"]["gitsubmodule-minor-patch"])
        self.assertEqual(actions["schedule"], {"interval": "weekly"})
        self.assertEqual(actions["labels"], ["dependencies"])
        self.assertEqual(fb._group_key("Docker Compose"), "docker-compose")

    def test_normalize_dir(self) -> None:
        self.assertEqual(fb._normalize_dir(" / "), "/")
        self.assertEqual(fb._normalize_dir("web/"), "/web")
        self.assertEqual(fb._normalize_dir("/web/*"), "/web/*")


class UncoveredRailsTests(unittest.TestCase):
    """Auditing an existing dependabot.yml."""

    RAILS = {"npm": ["/", "/web/app"], "pip": ["/"], "github-actions": ["/"]}

    def test_reports_missing_and_honours_globs(self) -> None:
        config = yaml.safe_dump(
            {
                "version": 2,
                "updates": [
                    "not-a-mapping",
                    {"package-ecosystem": "npm", "directories": ["/", "/web/*"], "schedule": {"interval": "weekly"}},
                    {"package-ecosystem": "pip", "directories": "/", "schedule": {"interval": "weekly"}},
                    {"package-ecosystem": "pip", "directories": ["/", None], "schedule": {"interval": "weekly"}},
                    {"package-ecosystem": "pip", "directory": ["/"], "schedule": {"interval": "weekly"}},
                    {"package-ecosystem": "github-actions", "directory": "/"},
                    {"package-ecosystem": "github-actions", "directory": "/", "schedule": "weekly"},
                    {"package-ecosystem": "pip", "directory": "/", "schedule": {"interval": "weekly"}},
                ],
            }
        )
        self.assertEqual(fb.uncovered_rails(self.RAILS, config), [("github-actions", "/")])

    def test_invalid_shapes_cover_nothing(self) -> None:
        config = yaml.safe_dump(
            {"updates": [{"package-ecosystem": "pip", "directories": "/", "schedule": {"interval": "weekly"}}]}
        )
        self.assertIn(("pip", "/"), fb.uncovered_rails(self.RAILS, config))

    def test_unparseable_and_odd_documents_cover_nothing(self) -> None:
        everything = [("npm", "/"), ("npm", "/web/app"), ("pip", "/"), ("github-actions", "/")]
        for text in ("version: [unclosed", "- a list", "version: 2\nupdates: nope\n", ""):
            with self.subTest(text=text):
                self.assertEqual(fb.uncovered_rails(self.RAILS, text), everything)


class LanguageAndGateTests(unittest.TestCase):
    """Language detection and the gate table."""

    def test_detect_languages(self) -> None:
        paths = ["", "a.py", "web/x.tsx", "b.mjs", "node_modules/z.go", "main.go", "x.kt", "y.cs", "README.md"]
        self.assertEqual(fb.detect_languages(paths), ("python", "ts", "js", "go", "kotlin", "csharp"))
        # case-sensitive, exactly like the lean gate's git ls-files globs
        self.assertEqual(fb.detect_languages(["main.CPP", "app.PY"]), ())
        self.assertEqual(fb.detect_languages(["a.c", "a.h", "b.hpp"]), ("cpp", "c"))
        self.assertEqual(fb.detect_languages(["only.h"]), ())

    def test_gate_rows_full_and_empty(self) -> None:
        rows = fb.gate_rows(("python", "ts", "go"))
        self.assertIn("ruff check", rows[0][1])
        self.assertIn("oxlint", rows[0][1])
        self.assertIn("tsc --noEmit", rows[1][1])
        self.assertIn("coverage-gate.sh", rows[2][1])
        empty = fb.gate_rows(())
        self.assertTrue(empty[0][1].startswith("n/a"))
        self.assertTrue(empty[1][1].startswith("n/a"))
        self.assertTrue(empty[2][1].startswith("n/a"))
        js_only = fb.gate_rows(("js", "cpp"))
        self.assertTrue(js_only[1][1].startswith("n/a"))
        self.assertIn("coverage-gate.sh", js_only[2][1])

    def test_gate_six_states_the_floor_it_actually_enforces(self) -> None:
        self.assertIn("T0 floor", fb.gate_rows(("python",))[5][2])
        self.assertEqual(fb.gate_rows(("python",), "any")[5][2], "zero known advisories, any severity")


class LocalCommandsTests(unittest.TestCase):
    """CONTRIBUTING.md's language-specific commands."""

    def test_pytest_detection_mirrors_the_lean_gate(self) -> None:
        for name in ("pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini"):
            with self.subTest(name=name):
                self.assertTrue(fb._python_uses_pytest(_reader({name: "[tool:pytest]"})))
        self.assertFalse(fb._python_uses_pytest(_reader({"pyproject.toml": "[project]"})))
        self.assertFalse(fb._python_uses_pytest(_reader({"requirements-dev.txt": "pytest"})))

    def test_coverage_scripts(self) -> None:
        files = {
            "package.json": json.dumps({"scripts": {"test:coverage": "vitest --coverage"}}),
            "web/package.json": json.dumps({"scripts": {"test": "vitest"}}),
            "ui/package.json": json.dumps({"scripts": {"test:coverage": "jest --coverage"}}),
            "bad/package.json": "{nope",
            "list/package.json": "[]",
            "odd/package.json": json.dumps({"scripts": ["test:coverage"]}),
            "deep/a/package.json": json.dumps({"scripts": {"test:coverage": "x"}}),
        }
        paths = list(files) + ["node_modules/package.json", "empty/package.json"]
        self.assertEqual(fb._coverage_scripts(_reader(files), paths), [".", "ui"])

    def test_local_commands_variants(self) -> None:
        text = fb.local_commands(("python",), _reader({"pyproject.toml": "pytest"}), ["pyproject.toml"])
        self.assertIn("python -m pytest --cov --cov-branch", text)
        self.assertIn("coverage run --branch -m unittest discover", fb.local_commands(("python",), _reader({}), []))
        js_files = {"package.json": json.dumps({"scripts": {"test:coverage": "c8 node --test"}})}
        self.assertIn(
            "(`.`): `npm ci && npm run test:coverage`", fb.local_commands(("js",), _reader(js_files), ["package.json"])
        )
        self.assertIn("add a `test:coverage` npm script", fb.local_commands(("ts",), _reader({}), []))
        self.assertIn("coverage-gate.sh", fb.local_commands(("cpp",), _reader({}), []))
        self.assertEqual(fb.local_commands(("kotlin",), _reader({}), []), "")


class TemplateTests(unittest.TestCase):
    """Rendering the real fleet templates."""

    def test_public_context_and_renders(self) -> None:
        facts = _facts(["app.py", "Main.java", "pyproject.toml"])
        context = fb.template_context(facts, _reader({"pyproject.toml": "pytest"}), PIN)
        self.assertIn("security/advisories/new", context["reporting_channel"])
        self.assertIn("If that form is unavailable to you", context["reporting_channel"])
        self.assertEqual(context["security_contact_url"], "https://github.com/Prekzursil/demo/security/advisories/new")
        self.assertIn("- **SAST** — opengrep in the lean gate (`quality / quality`).", context["security_controls"])
        self.assertIn("*extended* query suite", context["security_controls"])
        self.assertIn("push protection", context["security_controls"])
        self.assertIn("**Java**", context["extra_controls"])
        self.assertEqual(context["security_policy_path"], "SECURITY.md")
        self.assertEqual(tomllib.loads(fb.render_template("osv-scanner.toml", context)), {})
        for name in (
            "SECURITY.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "PULL_REQUEST_TEMPLATE.md",
            "AGENTS.md",
            "opengrep-README.md",
            "verify",
        ):
            with self.subTest(name=name):
                rendered = fb.render_template(name, context)
                self.assertNotIn("${", rendered.replace("${QZP_DIR", "").replace("${XDG_CACHE_HOME", ""))
                self.assertNotIn("\n\n\n", rendered)
                self.assertTrue(rendered.endswith("\n") and not rendered.endswith("\n\n"))
        verify = fb.render_template("verify", context)
        self.assertIn('root="$(git rev-parse --show-toplevel)"', verify)
        self.assertIn(
            "grep -rhE '^[^#]*uses:.*reusable-quality\\.yml@[0-9a-f]{40}' \"$root/.github/workflows\"", verify
        )
        self.assertIn("${QZP_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/qzp-verify/$pin}", verify)
        self.assertIn('mkdir -p "$(dirname "$qzp")"', verify)
        self.assertIn("checkout --quiet --force --detach", verify)
        self.assertIn('git -C "$qzp" clean -ffdq', verify)
        self.assertIn('exec bash "$qzp/scripts/quality/preflight.sh" "$@"', verify)
        for form in fb.ISSUE_FORMS:
            with self.subTest(form=form):
                self.assertIsInstance(yaml.safe_load(fb.render_template("ISSUE_TEMPLATE/" + form, context)), dict)
        caller = yaml.safe_load(fb.render_template("quality.yml", context))
        self.assertEqual(caller["jobs"]["quality"], {"uses": CALLER_PREFIX + PIN})
        self.assertEqual(caller["concurrency"]["group"], "quality-${{ github.ref }}")
        self.assertEqual(caller[True]["merge_group"], {"types": ["checks_requested"]})

    def test_default_branch_is_a_quoted_yaml_scalar(self) -> None:
        facts = fb.RepoFacts("Prekzursil/demo", "release,prod", False, ("a.py",))
        caller = yaml.safe_load(fb.render_template("quality.yml", fb.template_context(facts, _reader({}), PIN)))
        self.assertEqual(caller[True]["push"]["branches"], ["release,prod"])
        self.assertEqual(caller[True]["pull_request"]["branches"], ["release,prod"])

    def test_zero_tolerance_caller_passes_the_floor(self) -> None:
        context = fb.template_context(_facts(["app.py"]), _reader({}), PIN, "any")
        caller = yaml.safe_load(fb.render_template("quality.yml", context))
        self.assertEqual(caller["jobs"]["quality"]["with"], {"deps-severity-floor": "any"})
        self.assertIn("zero known advisories, any severity", context["gate_rows"])

    def test_links_follow_the_retained_security_policy(self) -> None:
        context = fb.template_context(_facts([".github/SECURITY.md"]), _reader({}), PIN)
        self.assertEqual(context["security_policy_path"], ".github/SECURITY.md")
        self.assertIn("[SECURITY.md](.github/SECURITY.md)", fb.render_template("CONTRIBUTING.md", context))
        bug = yaml.safe_load(fb.render_template("ISSUE_TEMPLATE/bug_report.yml", context))
        self.assertIn("/blob/main/.github/SECURITY.md", bug["body"][0]["attributes"]["value"])

    def test_fork_notes(self) -> None:
        facts = fb.RepoFacts("Prekzursil/demo", "main", False, ("a.py",), "upstream/demo")
        context = fb.template_context(facts, _reader({}), PIN)
        security = fb.render_template("SECURITY.md", context)
        self.assertIn("fork of [upstream/demo](https://github.com/upstream/demo)", security)
        self.assertIn("report one introduced by this fork's changes here", security)
        self.assertIn(
            "Changes that are useful beyond this fork belong upstream", fb.render_template("CONTRIBUTING.md", context)
        )
        plain = fb.template_context(_facts(["a.py"]), _reader({}), PIN)
        self.assertEqual((plain["fork_security_note"], plain["fork_contributing_note"]), ("", ""))
        self.assertNotIn("fork of", fb.render_template("SECURITY.md", plain))

    def test_private_context(self) -> None:
        context = fb.template_context(_facts(["a.py"], private=True), _reader({}), PIN)
        self.assertEqual(context["security_contact_url"], "https://github.com/Prekzursil/demo/blob/main/SECURITY.md")
        self.assertIn("visible only to people with access", context["reporting_channel"])
        self.assertIn("this repository is private", context["conduct_contact"])
        self.assertIn("This repository is private", context["reporting_channel"])
        self.assertIn("GitHub Advanced Security", context["security_controls"])
        self.assertNotIn("push protection", context["security_controls"])
        self.assertNotIn("not measured by the lean gate", context["extra_controls"])

    def test_missing_key_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "x.tmpl").write_text("hello ${nobody}\n\n\n\nend\n", encoding="utf-8")
            with self.assertRaises(KeyError):
                fb.render_template("x", {}, root=Path(tmp))
            self.assertEqual(fb.render_template("x", {"nobody": "you"}, root=Path(tmp)), "hello you\n\nend\n")


class SecurityPolicyTests(unittest.TestCase):
    """Which SECURITY.md files may be upgraded."""

    def test_upgradable(self) -> None:
        slug = "Prekzursil/demo"
        legacy = render_security_policy({"slug": slug}).replace("\n", "\r\n") + "\r\n"
        self.assertTrue(fb.is_upgradable_security_policy(legacy, slug))
        self.assertFalse(fb.is_upgradable_security_policy("# Our own policy\n", slug))
        original = fb.KNOWN_GENERIC_SECURITY_SHA256
        try:
            fb.KNOWN_GENERIC_SECURITY_SHA256 = frozenset({fb._stripped_sha256("generic\n")})
            self.assertTrue(fb.is_upgradable_security_policy("  generic  ", slug))
        finally:
            fb.KNOWN_GENERIC_SECURITY_SHA256 = original

    def test_plan_security_branches(self) -> None:
        context = fb.template_context(_facts([]), _reader({}), PIN)
        created = fb._plan_security(_facts([]), _reader({}), context)
        self.assertEqual((created.path, created.action), ("SECURITY.md", "create"))
        legacy = render_security_policy({"slug": "Prekzursil/demo"})
        replaced = fb._plan_security(_facts(["SECURITY.md"]), _reader({"SECURITY.md": legacy}), context)
        self.assertEqual(replaced.action, "replace")
        kept = fb._plan_security(_facts(["SECURITY.md"]), _reader({}), context)
        self.assertEqual(kept.action, "keep")
        nested = fb._plan_security(_facts([".github/SECURITY.md"]), _reader({".github/SECURITY.md": legacy}), context)
        self.assertEqual((nested.path, nested.action), (".github/SECURITY.md", "keep"))
        lower = fb._plan_security(_facts(["security.md"]), _reader({"security.md": legacy}), context)
        self.assertEqual((lower.path, lower.action), ("security.md", "keep"))


class CallerTests(unittest.TestCase):
    """Finding and re-pinning the lean-gate caller."""

    def test_caller_refs_reads_jobs_not_comments(self) -> None:
        text = (
            f"# example: uses: {CALLER_PREFIX}v0\n"
            "jobs:\n"
            f"  quality:\n    uses: {CALLER_PREFIX}v1\n"
            "  lint:\n    runs-on: ubuntu-latest\n"
            "  other:\n    uses: someone/else/.github/workflows/x.yml@v2\n"
            "  odd: 3\n"
        )
        self.assertEqual(fb.caller_refs(text), {"v1"})
        self.assertEqual(fb.caller_refs(f"# uses: {CALLER_PREFIX}v1\non: push\n"), set())
        self.assertEqual(fb.caller_refs("jobs: [1, 2]\n"), set())
        self.assertEqual(fb.caller_refs("- a list\n"), set())
        self.assertEqual(fb.caller_refs("jobs: [unclosed\n"), set())

    def test_bump_caller_handles_quoted_values(self) -> None:
        text = f"jobs:\n  q:\n    uses: \"{CALLER_PREFIX}v1\"\n  r:\n    uses: '{CALLER_PREFIX}v1'\n"
        self.assertEqual(fb.caller_refs(text), {"v1"})
        bumped = fb.bump_caller(text, PIN)
        self.assertEqual(fb.caller_refs(bumped), {PIN})

    def test_bump_caller_leaves_comments_alone(self) -> None:
        text = f"# was: uses: {CALLER_PREFIX}v0\njobs:\n  q:\n    uses: {CALLER_PREFIX}v1 # pinned\n"
        bumped = fb.bump_caller(text, PIN)
        self.assertIn(f"# was: uses: {CALLER_PREFIX}v0\n", bumped)
        self.assertIn(f"    uses: {CALLER_PREFIX}{PIN} # pinned\n", bumped)

    def test_caller_variants(self) -> None:
        context = fb.template_context(_facts([]), _reader({}), PIN)
        pinned = {
            ".github/workflows/q.yaml": f"name: quality\non: [push, pull_request]\njobs:\n  quality:\n"
            f"    uses: {CALLER_PREFIX}{PIN}\n"
        }
        regenerated = fb._plan_quality_caller(_facts(list(pinned)), _reader(pinned), context, PIN)
        self.assertEqual([(a.path, a.action) for a in regenerated], [(".github/workflows/q.yaml", "replace")])
        current = {".github/workflows/q.yaml": regenerated[0].content or ""}
        kept = fb._plan_quality_caller(_facts(list(current)), _reader(current), context, PIN)
        self.assertEqual([(a.path, a.action) for a in kept], [(".github/workflows/q.yaml", "keep")])
        hand = {
            ".github/workflows/q.yaml": f"name: quality\non: [push, pull_request]\njobs:\n  quality:\n"
            f"    uses: {CALLER_PREFIX}{PIN}\n  lint:\n    runs-on: ubuntu-latest\n"
        }
        pinned_hand = fb._plan_quality_caller(_facts(list(hand)), _reader(hand), context, PIN)
        self.assertEqual([(a.path, a.action) for a in pinned_hand], [(".github/workflows/q.yaml", "keep")])
        in_repo = fb._plan_quality_caller(_facts([fb.QUALITY_CALLER]), _reader({}), context, PIN)
        self.assertEqual([(a.path, a.action) for a in in_repo], [(fb.QUALITY_CALLER, "gap")])
        commented = {".github/workflows/ci.yml": f"# uses: {CALLER_PREFIX}v1\njobs: {{}}\n"}
        created = fb._plan_quality_caller(_facts(list(commented)), _reader(commented), context, PIN)
        self.assertEqual([(a.path, a.action) for a in created], [(fb.QUALITY_CALLER, "create")])


class PlanTests(unittest.TestCase):
    """End-to-end planning over in-memory checkouts."""

    def test_bare_repository_gets_everything(self) -> None:
        actions = fb.plan_baseline(_facts(["main.go", "go.mod", ".github/workflows/ci.yml"]), _reader({}), PIN)
        index = _by_path(actions)
        for path in (
            "SECURITY.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            ".github/PULL_REQUEST_TEMPLATE.md",
            "AGENTS.md",
            ".github/dependabot.yml",
            ".github/ISSUE_TEMPLATE/config.yml",
            ".github/ISSUE_TEMPLATE/bug_report.yml",
            ".github/ISSUE_TEMPLATE/feature_request.yml",
            ".quality/opengrep/README.md",
            ".quality/opengrep/general-security.yaml",
            ".semgrepignore",
            "osv-scanner.toml",
            "scripts/verify",
            fb.QUALITY_CALLER,
        ):
            with self.subTest(path=path):
                self.assertEqual(index[path].action, "create")
                self.assertTrue(index[path].writes)
        self.assertTrue(index["scripts/verify"].executable)
        self.assertFalse(index["AGENTS.md"].executable)
        self.assertNotIn(".quality/opengrep/python-security.yaml", index)
        self.assertNotIn(".quality/opengrep/javascript-security.yaml", index)
        self.assertEqual(index[".semgrepignore"].content, fb.SEMGREPIGNORE_SOURCE.read_text(encoding="utf-8"))
        rails = yaml.safe_load(index[".github/dependabot.yml"].content or "")["updates"]
        self.assertEqual([rail["package-ecosystem"] for rail in rails], ["github-actions", "gomod"])

    def test_first_workflow_gets_an_actions_rail_and_language_rules(self) -> None:
        actions = fb.plan_baseline(_facts(["app.py", "web/main.ts", "requirements.txt"]), _reader({}), PIN)
        index = _by_path(actions)
        rails = yaml.safe_load(index[".github/dependabot.yml"].content or "")["updates"]
        self.assertEqual([rail["package-ecosystem"] for rail in rails], ["github-actions", "pip"])
        for name in ("general-security.yaml", "python-security.yaml", "javascript-security.yaml"):
            with self.subTest(name=name):
                self.assertEqual(
                    index[fb.OPENGREP_DIR + name].content,
                    (fb.OPENGREP_SOURCE / name).read_text(encoding="utf-8"),
                )

    def test_governed_repository_is_kept_bumped_and_audited(self) -> None:
        caller = f"name: quality\non:\n  pull_request:\njobs:\n  quality:\n    uses: {CALLER_PREFIX}v1\n"
        files = {
            ".github/dependabot.yml": "version: 2\nupdates:\n  - package-ecosystem: npm\n    directory: /\n",
            ".github/workflows/quality.yml": caller,
            ".github/workflows/lint.yml": "jobs: {}\n",
        }
        paths = list(files) + [
            "SECURITY.md",
            "docs/CONTRIBUTING.md",
            ".github/CODE_OF_CONDUCT.md",
            ".github/pull_request_template.md",
            "agents.md",
            ".github/ISSUE_TEMPLATE/bug.md",
            ".github/workflows/notes.txt",
            ".quality/opengrep/custom.yaml",
            ".semgrepignore",
            "osv-scanner.toml",
            "scripts/verify",
            "package.json",
        ]
        index = _by_path(fb.plan_baseline(_facts(paths), _reader(files), PIN))
        for path in (
            ".quality/opengrep/",
            ".semgrepignore",
            "osv-scanner.toml",
            "SECURITY.md",
            "docs/CONTRIBUTING.md",
            ".github/CODE_OF_CONDUCT.md",
            ".github/pull_request_template.md",
            "agents.md",
            ".github/ISSUE_TEMPLATE/",
            "scripts/verify",
        ):
            with self.subTest(path=path):
                self.assertEqual(index[path].action, "keep")
        self.assertEqual(index[".github/dependabot.yml"].action, "gap")
        self.assertIn("github-actions /", index[".github/dependabot.yml"].reason)
        regenerated = index[".github/workflows/quality.yml"]
        self.assertEqual(regenerated.action, "replace")
        self.assertIn(f"reusable-quality.yml@{PIN}", regenerated.content or "")
        self.assertEqual(regenerated.reason, f"thin caller regenerated from the fleet template (pin v1 -> {PIN})")
        files[".github/workflows/quality.yml"] = caller + "  lint:\n    runs-on: ubuntu-latest\n"
        bump = _by_path(fb.plan_baseline(_facts(paths), _reader(files), PIN))[".github/workflows/quality.yml"]
        self.assertEqual(bump.action, "bump")
        self.assertIn(f"reusable-quality.yml@{PIN}", bump.content or "")
        self.assertEqual(bump.reason, f"pin v1 -> {PIN}")

    def test_legacy_issue_template_is_kept(self) -> None:
        context = fb.template_context(_facts([]), _reader({}), PIN)
        actions = fb._plan_issue_forms(_facts([".github/issue_template.md"]), context)
        self.assertEqual([(a.path, a.action) for a in actions], [(".github/issue_template.md", "keep")])

    def test_readme_only_opengrep_dir_gets_rules_but_keeps_its_readme(self) -> None:
        context = fb.template_context(_facts([]), _reader({}), PIN)
        actions = fb._plan_gate_configs(_facts([".quality/opengrep/README.md", "a.py"]), _reader({}), context)
        written = [a.path for a in actions if a.action == "create"]
        self.assertNotIn(".quality/opengrep/README.md", written)
        self.assertIn(".quality/opengrep/general-security.yaml", written)
        self.assertIn(".quality/opengrep/python-security.yaml", written)

    def test_dependabot_that_ignores_majors_is_a_gap(self) -> None:
        config = (
            "version: 2\nupdates:\n  - package-ecosystem: github-actions\n    directory: /\n"
            "    schedule:\n      interval: weekly\n"
            "    ignore:\n      - dependency-name: '*'\n        update-types: ['version-update:semver-major']\n"
        )
        files = {".github/dependabot.yml": config}
        action = fb._plan_dependabot(_facts(list(files) + [".github/workflows/ci.yml"]), _reader(files))
        self.assertEqual(action.action, "gap")
        self.assertIn("semver-major updates are ignored", action.reason)
        self.assertNotIn("without a Dependabot rail", action.reason)

    def test_ignores_majors_shapes(self) -> None:
        self.assertFalse(fb.ignores_majors("version: [unclosed"))
        self.assertFalse(fb.ignores_majors("- a list"))
        self.assertFalse(fb.ignores_majors("updates: [1, {ignore: 3}, {ignore: [1, {update-types: x}]}]"))
        self.assertFalse(fb.ignores_majors("updates: [{ignore: [{update-types: [version-update:semver-minor]}]}]"))
        self.assertTrue(fb.ignores_majors("updates: [{ignore: [{update-types: [version-update:semver-major]}]}]"))

    def test_managed_rules_grow_with_the_languages_and_curated_rules_are_kept(self) -> None:
        context = fb.template_context(_facts([]), _reader({}), PIN)
        general = (fb.OPENGREP_SOURCE / "general-security.yaml").read_text(encoding="utf-8")
        managed = {fb.OPENGREP_DIR + "general-security.yaml": general.replace("\n", "\r\n")}
        grown = fb._plan_gate_configs(_facts(list(managed) + ["app.py"]), _reader(managed), context)
        written = {a.path: a.reason for a in grown if a.action == "create"}
        self.assertEqual(
            list(written),
            [
                fb.OPENGREP_DIR + "README.md",
                fb.OPENGREP_DIR + "python-security.yaml",
                ".semgrepignore",
                "osv-scanner.toml",
            ],
        )
        self.assertEqual(written[fb.OPENGREP_DIR + "python-security.yaml"], "gate 4 has no rule for this language")
        edited = {fb.OPENGREP_DIR + "general-security.yaml": general + "# local tweak\n"}
        kept = fb._plan_gate_configs(_facts(list(edited) + ["app.py"]), _reader(edited), context)
        self.assertEqual(kept[0].action, "keep")
        own = {fb.OPENGREP_DIR + "custom.yml": "rules: []\n"}
        self.assertEqual(fb._plan_gate_configs(_facts(list(own)), _reader(own), context)[0].action, "keep")

    def test_scope_that_drops_first_party_code_is_a_gap(self) -> None:
        context = fb.template_context(_facts([]), _reader({}), PIN)
        files = {".semgrepignore": "# reasoned\nvendor/\ntests/\n*_test.go\nresearch/\n"}
        actions = fb._plan_gate_configs(_facts(list(files)), _reader(files), context)
        scope = _by_path(actions)[".semgrepignore"]
        self.assertEqual(scope.action, "gap")
        self.assertIn("tests/, *_test.go", scope.reason)
        fine = {".semgrepignore": "vendor/\nresearch/\n"}
        self.assertEqual(
            _by_path(fb._plan_gate_configs(_facts(list(fine)), _reader(fine), context))[".semgrepignore"].action, "keep"
        )

    def test_compiled_lane_prerequisites(self) -> None:
        gaps = fb._plan_compiled_lane(_facts(["svc/go.mod", "svc/main.go", "svc/main_test.go"]))
        self.assertEqual([a.path for a in gaps], ["go.mod", "scripts/coverage-gate.sh"])
        cpp = fb._plan_compiled_lane(_facts(["src/a.cpp", "tests/a_test.cpp"]))
        self.assertEqual([a.path for a in cpp], ["scripts/coverage-gate.sh"])
        ready = fb._plan_compiled_lane(_facts(["go.mod", "main.go", "main_test.go", "scripts/coverage-gate.sh"]))
        self.assertEqual(ready, [])
        self.assertEqual(fb._plan_compiled_lane(_facts(["main.go", "go.mod"])), [])
        self.assertEqual(fb._plan_compiled_lane(_facts(["lib.cpp"])), [])

    def test_caller_contract(self) -> None:
        good = f"name: quality\non: [push, pull_request]\njobs:\n  quality:\n    uses: {CALLER_PREFIX}v1\n"
        self.assertEqual(fb.caller_contract_gap(good), "")
        self.assertEqual(fb.caller_contract_gap(good.replace("[push, pull_request]", "pull_request")), "")
        self.assertIn("does not run on pull_request", fb.caller_contract_gap(good.replace(", pull_request", "")))
        self.assertIn("does not run on pull_request", fb.caller_contract_gap("on: 3\n"))
        self.assertEqual(fb.caller_contract_gap(good.replace("name: quality", "name: ci")), "")
        self.assertIn("job must be `quality`", fb.caller_contract_gap(good.replace("  quality:", "  lint:")))
        renamed = good.replace("    uses:", "    name: lean\n    uses:")
        self.assertIn("job must be `quality`", fb.caller_contract_gap(renamed))
        self.assertIn("deps-severity-floor: any", fb.caller_contract_gap(good, "any"))
        with_floor = good + "    with:\n      deps-severity-floor: any\n"
        self.assertEqual(fb.caller_contract_gap(with_floor, "any"), "")
        self.assertIn("does not run on pull_request", fb.caller_contract_gap("- a list\n"))
        context = fb.template_context(_facts([]), _reader({}), PIN)
        broken = {".github/workflows/quality.yml": good.replace("[push, pull_request]", "[push]")}
        flagged = fb._plan_quality_caller(_facts(list(broken)), _reader(broken), context, PIN)
        self.assertEqual([(a.path, a.action) for a in flagged], [(".github/workflows/quality.yml", "gap")])
        folded = {
            ".github/workflows/quality.yml": "name: quality\non: pull_request\njobs:\n  quality:\n    uses: >-\n"
            f"      {CALLER_PREFIX}v1\n  lint:\n    runs-on: ubuntu-latest\n"
        }
        stuck = fb._plan_quality_caller(_facts(list(folded)), _reader(folded), context, PIN)
        self.assertEqual([a.action for a in stuck], ["gap"])
        self.assertIn("could not re-pin v1 automatically", stuck[0].reason)

    def test_rst_and_txt_community_files_count(self) -> None:
        context = fb.template_context(_facts(["CONTRIBUTING.rst", "docs/CODE_OF_CONDUCT.txt"]), _reader({}), PIN)
        self.assertEqual(context["conduct_policy_path"], "docs/CODE_OF_CONDUCT.txt")
        actions = _by_path(fb.plan_baseline(_facts(["CONTRIBUTING.rst", "docs/CODE_OF_CONDUCT.txt"]), _reader({}), PIN))
        self.assertEqual(actions["CONTRIBUTING.rst"].action, "keep")
        self.assertEqual(actions["docs/CODE_OF_CONDUCT.txt"].action, "keep")
        self.assertIn(
            "[Contributor Covenant 2.1](docs/CODE_OF_CONDUCT.txt)",
            fb.render_template("CONTRIBUTING.md", context),
        )

    def test_osv_ignores_must_be_reasoned_and_dated(self) -> None:
        today = datetime.date(2026, 10, 24)
        good = '[[IgnoredVulns]]\nid = "GHSA-1"\nignoreUntil = 2026-12-31\nreason = "unreachable"\n'
        self.assertEqual(fb.osv_ignore_problems(good, today), [])
        stamped = good.replace("2026-12-31", "2026-12-31T00:00:00Z")
        self.assertEqual(fb.osv_ignore_problems(stamped, today), [])
        self.assertEqual(fb.osv_ignore_problems("", today), [])
        self.assertIn("does not parse", fb.osv_ignore_problems("[[broken", today)[0])
        expired = good.replace("2026-12-31", "2026-01-01")
        self.assertEqual(fb.osv_ignore_problems(expired, today), ["ignore GHSA-1 expired on 2026-01-01"])
        bare = '[[IgnoredVulns]]\nid = "GHSA-2"\n'
        self.assertEqual(
            fb.osv_ignore_problems(bare, today),
            ["ignore GHSA-2 has no reason", "ignore GHSA-2 has no ignoreUntil date"],
        )
        self.assertEqual(len(fb.osv_ignore_problems("IgnoredVulns = [1]\n", today)), 2)
        self.assertEqual(len(fb.osv_ignore_problems('[IgnoredVulns]\nid = "x"\n', today)), 2)
        context = fb.template_context(_facts([]), _reader({}), PIN)
        files = {"osv-scanner.toml": bare}
        actions = fb._plan_gate_configs(_facts(list(files)), _reader(files), context, today)
        self.assertEqual(_by_path(actions)["osv-scanner.toml"].action, "gap")
        upper = fb._plan_gate_configs(_facts(["OSV-SCANNER.TOML", ".SEMGREPIGNORE"]), _reader({}), context)
        written = [a.path for a in upper if a.action == "create"]
        self.assertIn("osv-scanner.toml", written)
        self.assertIn(".semgrepignore", written)

    def test_rails_gate_six_cannot_scan(self) -> None:
        def gap(ecosystem: str, directory: str) -> str:
            return f"{ecosystem} {directory} has no file osv-scanner reads ({' / '.join(fb._SCANNED_LOCKFILES[ecosystem])})"

        rails = {
            "swift": ["/"],
            "bun": ["/"],
            "nuget": ["/src", "/tools"],
            "npm": ["/", "/web", "/web/sub"],
            "gradle": ["/", "/app"],
            "pip": ["/", "/svc", "/lib"],
            "cargo": ["/", "/crates/a"],
            "uv": ["/py"],
            "composer": ["/"],
            "gomod": ["/"],
        }
        paths = [
            "package-lock.json",
            "bun.lock",
            "web/package.json",
            "web/sub/yarn.lock",
            "gradle/verification-metadata.xml",
            "verification-metadata.xml",
            "src/App.deps.json",
            "requirements/requirements-dev.txt",
            "svc/requirements/base.txt",
            "lib/pyproject.toml",
            "lib/nested/requirements.txt",
            "Cargo.lock",
            "crates/a/Cargo.toml",
            "other/uv.lock",
            "vendor/x/composer.lock",
        ]
        self.assertEqual(
            fb.unscanned_rails(rails, paths),
            [
                "Swift packages (/)",
                gap("npm", "/web"),
                gap("pip", "/svc"),
                gap("uv", "/py"),
                gap("composer", "/"),
                gap("gradle", "/app"),
                gap("nuget", "/tools"),
            ],
        )
        self.assertTrue(fb._readable("app/gradle/verification-metadata.xml", fb._SCANNED_LOCKFILES["gradle"]))
        self.assertFalse(fb._readable("svc/requirements/base.txt", fb._SCANNED_LOCKFILES["pip"]))
        self.assertIsNone(fb._rail_of("other/uv.lock", ["/py"]))
        self.assertEqual(fb._rail_of("web/sub/yarn.lock", ["/", "/web", "/web/sub"]), "/web/sub")
        plan = _by_path(fb.plan_baseline(_facts(["Package.swift", "a.swift"]), _reader({}), PIN))
        self.assertIn("Swift packages", plan[fb.GATE6_SCOPE].reason)
        self.assertNotIn(fb.GATE6_SCOPE, _by_path(fb.plan_baseline(_facts(["a.py"]), _reader({}), PIN)))

    def test_jsts_tests_need_a_coverage_script(self) -> None:
        files = {"package.json": json.dumps({"scripts": {"test": "vitest run"}})}
        gaps = fb._plan_jsts_lane(_facts(["package.json", "src/a.ts", "src/a.test.ts"]), _reader(files))
        self.assertEqual([a.path for a in gaps], ["package.json"])
        spec = fb._plan_jsts_lane(_facts(["src/__tests__/b.js"]), _reader({}))
        self.assertEqual([a.action for a in spec], ["gap"])
        covered = {"package.json": json.dumps({"scripts": {"test": "vitest run --coverage"}})}
        self.assertEqual(fb._plan_jsts_lane(_facts(["package.json", "a.test.js"]), _reader(covered)), [])
        script = {"package.json": json.dumps({"scripts": {"test:coverage": "c8 node --test"}})}
        self.assertEqual(fb._plan_jsts_lane(_facts(["package.json", "a.spec.mjs"]), _reader(script)), [])
        broken = {"package.json": "{nope", "web/package.json": "[]"}
        paths = ["package.json", "web/package.json", "a.test.js", "deep/x/package.json"]
        self.assertEqual(len(fb._plan_jsts_lane(_facts(paths), _reader(broken))), 1)
        self.assertEqual(fb._plan_jsts_lane(_facts(["a.js", "node_modules/x/y.test.js"]), _reader({})), [])
        self.assertEqual(fb._plan_jsts_lane(_facts(["a.py", "test_a.py"]), _reader({})), [])

    def test_managed_dependabot_is_regenerated(self) -> None:
        managed = fb.render_dependabot({"github-actions": ["/"]})
        files = {".github/dependabot.yml": managed.replace("\n", "\r\n")}
        current = fb._plan_dependabot(_facts(list(files) + [".github/workflows/ci.yml"]), _reader(files))
        self.assertEqual(current.action, "keep")
        grown = fb._plan_dependabot(_facts(list(files) + [".github/workflows/ci.yml", "go.mod"]), _reader(files))
        self.assertEqual(grown.action, "replace")
        self.assertIn("gomod", grown.content or "")

    def test_outside_languages_need_their_own_ci(self) -> None:
        bare = fb.plan_baseline(_facts(["src/Main.java", "src/App.kt", "src/lib.c"]), _reader({}), PIN)
        gap = _by_path(bare)[".github/workflows/"]
        self.assertEqual(gap.action, "gap")
        self.assertIn("C, Java, Kotlin source has no CI lane", gap.reason)
        caller_only = fb._plan_outside_languages(_facts(["Main.java", fb.QUALITY_CALLER, "notes/ci.yml"]), _reader({}))
        self.assertEqual([a.action for a in caller_only], ["gap"])
        scanning_only = fb._plan_outside_languages(
            _facts(["Main.java", ".github/workflows/codeql.yml", ".github/workflows/pages.yml"]), _reader({})
        )
        renamed_caller = {".github/workflows/lean.yml": f"jobs:\n  q:\n    uses: {CALLER_PREFIX}{PIN}\n"}
        self.assertEqual(
            [
                a.action
                for a in fb._plan_outside_languages(
                    _facts(["Main.java"] + list(renamed_caller)), _reader(renamed_caller)
                )
            ],
            ["gap"],
        )
        self.assertEqual([a.action for a in scanning_only], ["gap"])
        empty_ci = fb._plan_outside_languages(_facts(["Main.java", ".github/workflows/ci.yml"]), _reader({}))
        self.assertEqual([a.action for a in empty_ci], ["gap"])
        gradle = {".github/workflows/gradle.yaml": "jobs:\n  t:\n    steps:\n      - run: ./gradlew test\n"}
        own_ci = fb._plan_outside_languages(_facts(["Main.java"] + list(gradle)), _reader(gradle))
        self.assertEqual(own_ci, [])
        self.assertEqual(fb._plan_outside_languages(_facts(["app.py"]), _reader({})), [])

    def test_dependabot_variants(self) -> None:
        self.assertEqual(fb._plan_dependabot(_facts(["README.md"]), _reader({})).action, "keep")
        covered = {
            ".github/dependabot.yaml": "version: 2\nupdates:\n  - package-ecosystem: github-actions\n    directory: /\n"
            "    schedule:\n      interval: weekly\n"
        }
        action = fb._plan_dependabot(_facts(list(covered) + [".github/workflows/ci.yml"]), _reader(covered))
        self.assertEqual((action.path, action.action), (".github/dependabot.yaml", "keep"))
        unreadable = fb._plan_dependabot(_facts([".github/dependabot.yml", "go.mod"]), _reader({}))
        self.assertEqual(unreadable.action, "gap")


class TruthfulDocumentTests(unittest.TestCase):
    """The documents describe the repository as it is, not as the managed default."""

    def test_codeql_suite_ignores_comments(self) -> None:
        self.assertEqual(fb._codeql_suite("# queries: security-extended\nsteps: []\n"), "default")
        self.assertEqual(fb._codeql_suite("with:\n  queries: 'security-extended'  # pinned\n"), "security-extended")
        self.assertEqual(
            fb._codeql_suite("queries: security-extended,security-and-quality\n"),
            "security-extended,security-and-quality",
        )
        self.assertEqual(fb._codeql_suite("queries:\n  - uses: security-extended\n"), "configured")
        self.assertEqual(fb._codeql_suite("with:\n  config-file: ./.github/codeql.yml\n"), "configured")

    def test_codeql_workflow_detection(self) -> None:
        self.assertIsNone(fb.codeql_workflow(_facts(["a.py"]), _reader({})))
        reusable = "Prekzursil/quality-zero-platform/.github/workflows/reusable-codeql.yml@" + PIN
        files = {
            ".github/workflows/ci.yml": "jobs: {}\n",
            ".github/workflows/codeql.yml": f"jobs:\n  codeql:\n    uses: {reusable}\n",
            ".github/workflows/notes.md": "uses: github/codeql-action/init@v3\n",
        }
        found = fb.codeql_workflow(_facts(list(files)), _reader(files))
        self.assertEqual(found, (".github/workflows/codeql.yml", None, True))
        own = {
            ".github/workflows/scan.yaml": "# uses: x/reusable-codeql.yml@v1\nsteps:\n"
            "  - uses: github/codeql-action/init@v3\n    with:\n      queries: security-and-quality\n"
        }
        self.assertEqual(
            fb.codeql_workflow(_facts(list(own)), _reader(own)),
            (".github/workflows/scan.yaml", "security-and-quality", False),
        )
        commented = {".github/workflows/scan.yml": f"# uses: {reusable}\n# - uses: github/codeql-action/init@v3\n"}
        self.assertIsNone(fb.codeql_workflow(_facts(list(commented)), _reader(commented)))

    def test_codeql_control_variants(self) -> None:
        self.assertIn("GitHub Advanced Security", fb._codeql_control(_facts(["a.py"], private=True), _reader({})))
        self.assertIn("GitHub default setup", fb._codeql_control(_facts(["a.py"]), _reader({})))
        reusable = "Prekzursil/quality-zero-platform/.github/workflows/reusable-codeql.yml@v1"
        managed = {".github/workflows/codeql.yml": f"jobs:\n  codeql:\n    uses: {reusable}\n"}
        text = fb._codeql_control(_facts(list(managed)), _reader(managed))
        self.assertIn(
            "advanced setup: `.github/workflows/codeql.yml` calls quality-zero-platform's `reusable-codeql.yml` at "
            "the commit it pins",
            text,
        )
        self.assertIn("any severity cannot be merged", text)
        bare = {".github/workflows/codeql.yml": "steps:\n  - uses: github/codeql-action/init@v3\n"}
        self.assertIn(
            "`.github/workflows/codeql.yml` runs the default query suite",
            fb._codeql_control(_facts(list(bare)), _reader(bare)),
        )
        named = {
            ".github/workflows/codeql.yml": bare[".github/workflows/codeql.yml"] + "      queries: security-extended\n"
        }
        self.assertIn("runs the `security-extended` queries", fb._codeql_control(_facts(list(named)), _reader(named)))
        listed = {".github/workflows/codeql.yml": bare[".github/workflows/codeql.yml"] + "      config-file: c.yml\n"}
        self.assertIn(
            "runs the queries its configuration lists", fb._codeql_control(_facts(list(listed)), _reader(listed))
        )

    def test_repository_authored_files_are_described_as_such(self) -> None:
        own = {
            "scripts/verify": "#!/bin/sh\n./gradlew test\n",
            ".github/dependabot.yml": "version: 2\nupdates: []\n",
        }
        paths = list(own) + ["app.py", ".github/pull_request_template.md"]
        context = fb.template_context(_facts(paths), _reader(own), PIN)
        contributing = fb.render_template("CONTRIBUTING.md", context)
        self.assertIn("this repository's own verification script", contributing)
        self.assertIn("4. Open a pull request and fill in the pull-request template.\n", contributing)
        self.assertIn("on the schedule set in\n`.github/dependabot.yml`", contributing)
        self.assertNotIn("maps 1:1", contributing)
        self.assertIn("(this repository's own configuration)", context["extra_controls"])
        self.assertIn("Dependabot alerts and security updates.", context["security_controls"])
        self.assertIn("(this\n  repository's own checks)", fb.render_template("AGENTS.md", context))
        for name in ("CONTRIBUTING.md", "AGENTS.md"):
            with self.subTest(name=name):
                rendered = fb.render_template(name, context)
                self.assertNotIn("${", rendered)
                self.assertNotIn("\n\n\n", rendered)

    def test_managed_files_keep_the_managed_wording(self) -> None:
        managed = {
            "scripts/verify": "# means what a CI pass means. Managed by quality-zero-platform\n",
            ".github/dependabot.yml": fb.render_dependabot({"pip": ["/"]}).replace("\n", "\r\n"),
        }
        context = fb.template_context(_facts(list(managed) + ["app.py"]), _reader(managed), PIN)
        contributing = fb.render_template("CONTRIBUTING.md", context)
        self.assertIn("`scripts/verify` runs QZP's preflight", contributing)
        self.assertIn("security updates are grouped", contributing)
        self.assertIn("template; it maps 1:1 to what CI enforces.", contributing)
        self.assertIn("grouped security updates.", context["security_controls"])
        self.assertIn("It runs\n  every lane of the QZP lean gate", fb.render_template("AGENTS.md", context))


class ThinCallerTests(unittest.TestCase):
    """A thin lean-gate caller is platform-owned and regenerated with its inputs."""

    THIN = (
        f"name: quality\non:\n  pull_request:\n  workflow_dispatch:\njobs:\n  quality:\n    uses: {CALLER_PREFIX}v1\n"
    )

    def test_yaml_scalar_round_trips(self) -> None:
        for value in ("any", "backend frontend", "3.12", "true", "-", "on", "2026-01-01"):
            with self.subTest(value=value):
                self.assertEqual(yaml.safe_load(f"k: {fb._yaml_scalar(value)}")["k"], value)
        self.assertEqual(fb._yaml_scalar("backend/api"), "backend/api")
        self.assertEqual(fb._yaml_scalar("3.12"), '"3.12"')

    def test_caller_with(self) -> None:
        self.assertEqual(fb.caller_with({}, "t0"), "")
        self.assertEqual(fb.caller_with({}, "any"), "    with:\n      deps-severity-floor: any\n")
        self.assertEqual(
            fb.caller_with({"deps-severity-floor": "t0", "opengrep-paths": "backend frontend"}, "any"),
            '    with:\n      opengrep-paths: "backend frontend"\n      deps-severity-floor: any\n',
        )
        # a regeneration at the default floor never weakens a caller that already runs at zero tolerance
        self.assertEqual(
            fb.caller_with({"deps-severity-floor": "any"}, "t0"), "    with:\n      deps-severity-floor: any\n"
        )

    def test_thin_caller_shapes(self) -> None:
        self.assertEqual(fb.thin_caller(self.THIN), ({}, True))
        listed = self.THIN.replace("on:\n  pull_request:\n  workflow_dispatch:\n", "on: [pull_request]\n")
        self.assertEqual(fb.thin_caller(listed), ({}, False))
        scalar = self.THIN.replace("on:\n  pull_request:\n  workflow_dispatch:\n", "on: pull_request\n")
        self.assertEqual(fb.thin_caller(scalar), ({}, False))
        with_inputs = self.THIN + "    with:\n      opengrep-paths: src\n"
        self.assertEqual(fb.thin_caller(with_inputs), ({"opengrep-paths": "src"}, True))
        job_scoped = self.THIN + "    permissions:\n      contents: read\n"
        self.assertEqual(fb.thin_caller(job_scoped), ({}, True))
        manual = self.THIN.replace("  workflow_dispatch:\n", "  workflow_dispatch: {}\n")
        self.assertEqual(fb.thin_caller(manual), ({}, True))
        for broken in (
            "jobs: [unclosed\n",
            "- a list\n",
            self.THIN + "env:\n  A: b\n",
            self.THIN.replace("  pull_request:\n", "  push:\n"),
            self.THIN + "  lint:\n    runs-on: ubuntu-latest\n",
            self.THIN + "    secrets: inherit\n",
            self.THIN + "    with: nope\n",
            # YAML 1.1 reads `on` as a boolean; GitHub reads it as text, so the caller stays hand-written
            self.THIN + "    with:\n      opengrep-paths: on\n",
            self.THIN.replace(
                "  workflow_dispatch:\n", "  workflow_dispatch:\n    inputs:\n      x:\n        type: string\n"
            ),
            self.THIN + "permissions:\n  contents: write\n",
            self.THIN + "    permissions:\n      contents: write\n",
        ):
            with self.subTest(broken=broken):
                self.assertIsNone(fb.thin_caller(broken))

    def test_regeneration_carries_inputs_and_the_manual_trigger(self) -> None:
        context = fb.template_context(_facts([]), _reader({}), PIN)
        text = self.THIN + "    with:\n      opengrep-paths: src\n"
        files = {".github/workflows/quality.yml": text}
        action = fb._plan_quality_caller(_facts(list(files)), _reader(files), context, PIN)[0]
        self.assertEqual(action.action, "replace")
        self.assertEqual(action.reason, f"thin caller regenerated from the fleet template (pin v1 -> {PIN})")
        caller = yaml.safe_load(action.content or "")
        self.assertIn("workflow_dispatch", caller[True])
        self.assertEqual(caller["jobs"]["quality"]["with"], {"opengrep-paths": "src"})
        strict = fb.template_context(_facts([]), _reader({}), PIN, "any")
        zero = fb._plan_quality_caller(_facts(list(files)), _reader(files), strict, PIN)[0]
        self.assertTrue(zero.reason.endswith(", deps-severity-floor: any)"))
        self.assertEqual(
            yaml.safe_load(zero.content or "")["jobs"]["quality"]["with"],
            {"opengrep-paths": "src", "deps-severity-floor": "any"},
        )
        current = {".github/workflows/quality.yml": (zero.content or "").replace("\n", "\r\n")}
        kept = fb._plan_quality_caller(_facts(list(current)), _reader(current), strict, PIN)[0]
        self.assertEqual((kept.action, kept.reason), ("keep", "lean-gate caller is current"))


class VerifyAndFloorTests(unittest.TestCase):
    """The managed verify wrapper is upgraded in place; the documented floor follows the caller."""

    def test_plan_verify(self) -> None:
        context = fb.template_context(_facts([]), _reader({}), PIN)
        created = fb._plan_verify(_facts([]), _reader({}), context)
        self.assertEqual((created.action, created.executable), ("create", True))
        own = fb._plan_verify(_facts([fb.VERIFY_SCRIPT]), _reader({fb.VERIFY_SCRIPT: "make test\n"}), context)
        self.assertEqual((own.action, own.reason), ("keep", "repository-authored file"))
        current = {fb.VERIFY_SCRIPT: (created.content or "").replace("\n", "\r\n")}
        kept = fb._plan_verify(_facts(list(current)), _reader(current), context)
        self.assertEqual((kept.action, kept.reason), ("keep", "managed verify is current"))
        outdated = {fb.VERIFY_SCRIPT: "# Managed by quality-zero-platform\ngit checkout --detach x\n"}
        upgraded = fb._plan_verify(_facts(list(outdated)), _reader(outdated), context)
        self.assertEqual((upgraded.action, upgraded.executable), ("replace", True))
        self.assertEqual(upgraded.content, created.content)

    def test_effective_floor_never_drops_an_existing_any(self) -> None:
        strict = f"jobs:\n  quality:\n    uses: {CALLER_PREFIX}{PIN}\n    with:\n      deps-severity-floor: any\n"
        files = {
            ".github/workflows/a.yml": "jobs: [unclosed\n",
            ".github/workflows/b.yml": "- a list\n",
            ".github/workflows/c.yml": "jobs:\n  odd: 3\n  other:\n    uses: x/y/.github/workflows/z.yml@v1\n",
            ".github/workflows/d.yml": f"jobs:\n  quality:\n    uses: {CALLER_PREFIX}v1\n    with: nope\n",
            "notes/quality.yml": strict,
        }
        self.assertEqual(fb.effective_floor(_facts(list(files)), _reader(files), "t0"), "t0")
        files[".github/workflows/quality.yml"] = strict
        self.assertEqual(fb.effective_floor(_facts(list(files)), _reader(files), "t0"), "any")
        self.assertEqual(fb.effective_floor(_facts([]), _reader({}), "any"), "any")
        plan = _by_path(fb.plan_baseline(_facts(list(files)), _reader(files), PIN))
        self.assertIn("zero known advisories, any severity", plan["CONTRIBUTING.md"].content or "")


class ApplyAndIoTests(unittest.TestCase):
    """Writing, file listing and reading."""

    def test_apply_actions_writes_lf_marks_executables_and_skips_non_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            written = fb.apply_actions(
                root,
                [
                    fb.FileAction("a/b.md", "create", "missing", "one\ntwo\n"),
                    fb.FileAction("scripts/verify", "create", "missing", "#!/bin/sh\n", executable=True),
                    fb.FileAction("keep.md", "keep", "repository-authored file"),
                    fb.FileAction("odd.md", "create", "no content"),
                ],
            )
            self.assertEqual(written, ["a/b.md", "scripts/verify"])
            self.assertEqual((root / "a" / "b.md").read_bytes(), b"one\ntwo\n")
            self.assertFalse((root / "odd.md").exists())
            if os.name == "posix":
                self.assertTrue((root / "scripts" / "verify").stat().st_mode & stat.S_IXUSR)

    def test_apply_actions_never_creates_over_an_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SECURITY.md").write_text("ours\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                fb.apply_actions(
                    root,
                    [
                        fb.FileAction("CONTRIBUTING.md", "create", "missing", "new\n"),
                        fb.FileAction("SECURITY.md", "create", "missing", "theirs\n"),
                    ],
                )
            self.assertEqual((root / "SECURITY.md").read_text(encoding="utf-8"), "ours\n")
            self.assertFalse((root / "CONTRIBUTING.md").exists())
            fb.apply_actions(root, [fb.FileAction("SECURITY.md", "replace", "boilerplate", "fleet\n")])
            self.assertEqual((root / "SECURITY.md").read_text(encoding="utf-8"), "fleet\n")

    def test_apply_actions_never_writes_outside_the_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            with self.assertRaises(ValueError):
                fb.apply_actions(root, [fb.FileAction("../escape.md", "replace", "x", "x\n")])
            self.assertFalse((Path(tmp) / "escape.md").exists())
            link = root / "linked.md"
            try:
                link.symlink_to(Path(tmp) / "elsewhere.md")
            except OSError:  # symlinks need privileges on some Windows hosts
                return
            with self.assertRaises(ValueError):
                fb.apply_actions(root, [fb.FileAction("linked.md", "create", "missing", "x\n")])
            self.assertFalse((Path(tmp) / "elsewhere.md").exists())

    def test_list_checkout_files_includes_untracked_and_drops_deleted(self) -> None:
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout="a.py\0b/c.md\0a.py\0gone.md\0", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("", encoding="utf-8")
            (root / "b").mkdir()
            (root / "b" / "c.md").write_text("", encoding="utf-8")
            self.assertEqual(fb.list_checkout_files(root, runner=runner), ("a.py", "b/c.md"))
        self.assertEqual(calls[0][0][:2], ["git", "-C"])
        self.assertEqual(calls[0][0][-5:], ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
        self.assertTrue(calls[0][1]["check"])
        self.assertEqual(calls[0][1]["encoding"], "utf-8")

    def test_reader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ok.txt").write_text("fine", encoding="utf-8")
            (root / "bin.dat").write_bytes(b"\xff\xfe\x00bad")
            read = fb._reader(root)
            self.assertEqual(read("ok.txt"), "fine")
            self.assertIsNone(read("missing.txt"))
            self.assertIsNone(read("bin.dat"))

    def test_render_plan(self) -> None:
        text = fb.render_plan("Prekzursil/demo", [fb.FileAction("SECURITY.md", "keep", "reason")])
        self.assertEqual(text, "fleet baseline for Prekzursil/demo:\n  keep    SECURITY.md — reason")


class MainTests(unittest.TestCase):
    """The CLI."""

    def _run(self, argv: List[str], tracked: List[str]) -> tuple:
        """Run main() with a fake git listing; return (code, stdout, stderr)."""

        def runner(args, **_kwargs):
            return subprocess.CompletedProcess(args, 0, stdout="\0".join(tracked), stderr="")

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = fb.main(argv, runner=runner)
        return code, out.getvalue(), err.getvalue()

    def test_rejects_short_pin(self) -> None:
        code, _out, err = self._run(["--repo-dir", ".", "--slug", "Prekzursil/demo", "--qzp-pin", "v1"], [])
        self.assertEqual(code, 2)
        self.assertIn("full 40-hex", err)

    def test_plan_write_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("print('hi')\n", encoding="utf-8")
            plan_json = root / "plan.json"
            base = ["--repo-dir", str(root), "--slug", "Prekzursil/demo", "--qzp-pin", PIN]
            code, out, _err = self._run(base + ["--json-out", str(plan_json)], ["app.py"])
            self.assertEqual(code, 1)
            self.assertIn("create  SECURITY.md", out)
            payload = json.loads(plan_json.read_text(encoding="utf-8"))
            self.assertIn(
                {"path": "SECURITY.md", "action": "create", "reason": "no security policy in the repository"}, payload
            )
            write = [
                "--write",
                "--private",
                "--default-branch",
                "trunk",
                "--deps-severity-floor",
                "any",
                "--upstream",
                "up/demo",
            ]
            code, out, _err = self._run(base + write, ["app.py"])
            self.assertEqual(code, 0)
            self.assertIn("wrote SECURITY.md", out)
            self.assertIn("`trunk`", (root / "SECURITY.md").read_text(encoding="utf-8"))
            self.assertIn("fork of [up/demo]", (root / "SECURITY.md").read_text(encoding="utf-8"))
            caller = yaml.safe_load((root / fb.QUALITY_CALLER).read_text(encoding="utf-8"))
            self.assertEqual(caller["jobs"]["quality"]["with"], {"deps-severity-floor": "any"})
            tracked = [
                "app.py",
                "SECURITY.md",
                "CONTRIBUTING.md",
                "CODE_OF_CONDUCT.md",
                "AGENTS.md",
                ".github/PULL_REQUEST_TEMPLATE.md",
                ".github/ISSUE_TEMPLATE/config.yml",
                ".github/dependabot.yml",
                ".quality/opengrep/README.md",
                ".quality/opengrep/general-security.yaml",
                ".quality/opengrep/python-security.yaml",
                ".semgrepignore",
                "osv-scanner.toml",
                "scripts/verify",
                fb.QUALITY_CALLER,
            ]
            code, out, _err = self._run(base + ["--default-branch", "trunk"], tracked)
            self.assertEqual(code, 0, out)
            (root / ".github" / "dependabot.yml").write_text("version: 2\nupdates: []\n", encoding="utf-8")
            code, out, _err = self._run(base + ["--write"], tracked)
            self.assertEqual(code, 1)
            self.assertIn("gap     .github/dependabot.yml", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
