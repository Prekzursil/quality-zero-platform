"""Coverage for ``scripts/quality/fleet_baseline.py`` (the fleet governor baseline)."""

from __future__ import absolute_import

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Dict, List, Optional

import yaml  # type: ignore[import-untyped]

from scripts.quality import fleet_baseline as fb
from scripts.quality.render_repo_baseline import render_security_policy

PIN = "a" * 40
OTHER_PIN = "b" * 40


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
    """Path normalisation and exclusion."""

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
        }
        for path, ecosystem in cases.items():
            with self.subTest(path=path):
                self.assertEqual(fb._ecosystem_for(path), ecosystem)

    def test_non_manifests(self) -> None:
        for path in (".github/workflows/README.md", "requirements.lock", "README.md", "sub/.gitmodules"):
            with self.subTest(path=path):
                self.assertIsNone(fb._ecosystem_for(path))

    def test_rail_dir(self) -> None:
        self.assertEqual(fb._rail_dir("github-actions", ".github/workflows/ci.yml"), "/")
        self.assertEqual(fb._rail_dir("github-actions", "actions/setup/action.yml"), "/actions/setup")
        self.assertEqual(fb._rail_dir("github-actions", "action.yml"), "/")
        self.assertEqual(fb._rail_dir("devcontainers", ".devcontainer/devcontainer.json"), "/")
        self.assertEqual(fb._rail_dir("npm", "web/package.json"), "/web")

    def test_ancestry(self) -> None:
        self.assertFalse(fb._is_ancestor("/a", "/a"))
        self.assertTrue(fb._is_ancestor("/", "/a"))
        self.assertTrue(fb._is_ancestor("/a", "/a/b"))
        self.assertFalse(fb._is_ancestor("/a", "/ab"))
        self.assertEqual(fb._topmost({"/", "/a", "/b/c"}), {"/"})
        self.assertEqual(fb._topmost({"/a", "/a/b", "/c"}), {"/a", "/c"})


class DetectRailsTests(unittest.TestCase):
    """Rail detection over a whole checkout."""

    def test_detects_supersedes_and_collapses(self) -> None:
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
        rails = fb.detect_rails(paths)
        self.assertEqual(
            rails,
            {
                "bun": ["/"],
                "github-actions": ["/"],
                "gradle": ["/"],
                "npm": ["/web"],
                "pip": ["/tools"],
                "uv": ["/api"],
            },
        )

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
                    {"package-ecosystem": "npm", "directories": ["/", "/web/*", None]},
                    {"package-ecosystem": "pip", "directory": "/"},
                ],
            }
        )
        self.assertEqual(fb.uncovered_rails(self.RAILS, config), [("github-actions", "/")])

    def test_unparseable_and_odd_documents_cover_nothing(self) -> None:
        everything = [("npm", "/"), ("npm", "/web/app"), ("pip", "/"), ("github-actions", "/")]
        for text in ("version: [unclosed", "- a list", "version: 2\nupdates: nope\n", ""):
            with self.subTest(text=text):
                self.assertEqual(fb.uncovered_rails(self.RAILS, text), everything)


class LanguageAndGateTests(unittest.TestCase):
    """Language detection and the gate table."""

    def test_detect_languages(self) -> None:
        paths = ["", "a.py", "web/x.TSX", "b.mjs", "node_modules/z.go", "main.go", "x.kt", "y.cs", "README.md"]
        self.assertEqual(fb.detect_languages(paths), ("python", "ts", "js", "go", "kotlin", "csharp"))

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
        self.assertEqual(rows[5][2], "zero known advisories, any severity")


class LocalCommandsTests(unittest.TestCase):
    """CONTRIBUTING.md's language-specific commands."""

    def test_pytest_markers(self) -> None:
        for paths in (["pytest.ini"], ["conftest.py"], ["tests/conftest.py"]):
            with self.subTest(paths=paths):
                self.assertTrue(fb._python_uses_pytest(_reader({}), paths))
        self.assertTrue(fb._python_uses_pytest(_reader({"setup.cfg": "[tool:pytest]"}), []))
        self.assertFalse(fb._python_uses_pytest(_reader({"pyproject.toml": "[project]"}), []))

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
        pytest_files = {"pyproject.toml": "pytest"}
        text = fb.local_commands(("python",), _reader(pytest_files), ["pyproject.toml"])
        self.assertIn("python -m pytest --cov --cov-branch", text)
        unittest_text = fb.local_commands(("python",), _reader({}), [])
        self.assertIn("coverage run --branch -m unittest discover", unittest_text)
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
        self.assertIn("*extended* query suite", context["security_controls"])
        self.assertIn("push protection", context["security_controls"])
        self.assertIn("**Java**", context["extra_controls"])
        for name in ("SECURITY.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "PULL_REQUEST_TEMPLATE.md"):
            with self.subTest(name=name):
                rendered = fb.render_template(name, context)
                self.assertNotIn("${", rendered)
                self.assertNotIn("\n\n\n", rendered)
                self.assertTrue(rendered.endswith("\n") and not rendered.endswith("\n\n"))
        for form in fb.ISSUE_FORMS:
            with self.subTest(form=form):
                self.assertIsInstance(yaml.safe_load(fb.render_template("ISSUE_TEMPLATE/" + form, context)), dict)
        caller = yaml.safe_load(fb.render_template("quality.yml", context))
        self.assertEqual(
            caller["jobs"]["quality"]["uses"],
            f"Prekzursil/quality-zero-platform/.github/workflows/reusable-quality.yml@{PIN}",
        )
        self.assertEqual(caller["concurrency"]["group"], "quality-${{ github.ref }}")

    def test_private_context(self) -> None:
        context = fb.template_context(_facts(["a.py"], private=True), _reader({}), PIN)
        self.assertEqual(context["security_contact_url"], fb.MAINTAINER_URL)
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
            ".github/dependabot.yml",
            ".github/ISSUE_TEMPLATE/config.yml",
            ".github/ISSUE_TEMPLATE/bug_report.yml",
            ".github/ISSUE_TEMPLATE/feature_request.yml",
            fb.QUALITY_CALLER,
        ):
            with self.subTest(path=path):
                self.assertEqual(index[path].action, "create")
                self.assertTrue(index[path].writes)
        rails = yaml.safe_load(index[".github/dependabot.yml"].content or "")["updates"]
        self.assertEqual([rail["package-ecosystem"] for rail in rails], ["github-actions", "gomod"])

    def test_governed_repository_is_kept_bumped_and_audited(self) -> None:
        caller = (
            "jobs:\n  quality:\n    uses: Prekzursil/quality-zero-platform/.github/workflows/reusable-quality.yml@v1\n"
        )
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
            ".github/ISSUE_TEMPLATE/bug.md",
            ".github/workflows/notes.txt",
            "package.json",
        ]
        actions = fb.plan_baseline(_facts(paths), _reader(files), PIN)
        index = _by_path(actions)
        self.assertEqual(index["SECURITY.md"].action, "keep")
        self.assertEqual(index["docs/CONTRIBUTING.md"].action, "keep")
        self.assertEqual(index[".github/CODE_OF_CONDUCT.md"].action, "keep")
        self.assertEqual(index[".github/pull_request_template.md"].action, "keep")
        self.assertEqual(index[".github/ISSUE_TEMPLATE/"].action, "keep")
        self.assertEqual(index[".github/dependabot.yml"].action, "gap")
        self.assertIn("github-actions /", index[".github/dependabot.yml"].reason)
        bump = index[".github/workflows/quality.yml"]
        self.assertEqual(bump.action, "bump")
        self.assertIn(f"reusable-quality.yml@{PIN}", bump.content or "")
        self.assertEqual(bump.reason, f"pin v1 -> {PIN}")

    def test_caller_variants(self) -> None:
        context = fb.template_context(_facts([]), _reader({}), PIN)
        pinned = {
            ".github/workflows/q.yaml": f"uses: Prekzursil/quality-zero-platform/.github/workflows/"
            f"reusable-quality.yml@{PIN}\n",
        }
        kept = fb._plan_quality_caller(_facts(list(pinned)), _reader(pinned), context, PIN)
        self.assertEqual([(a.path, a.action) for a in kept], [(".github/workflows/q.yaml", "keep")])
        in_repo = fb._plan_quality_caller(_facts([fb.QUALITY_CALLER]), _reader({}), context, PIN)
        self.assertEqual([(a.path, a.action) for a in in_repo], [(fb.QUALITY_CALLER, "gap")])

    def test_dependabot_variants(self) -> None:
        self.assertEqual(fb._plan_dependabot(_facts(["README.md"]), _reader({})).action, "keep")
        covered = {
            ".github/dependabot.yaml": "version: 2\nupdates:\n  - package-ecosystem: github-actions\n    directory: /\n"
        }
        action = fb._plan_dependabot(_facts(list(covered) + [".github/workflows/ci.yml"]), _reader(covered))
        self.assertEqual((action.path, action.action), (".github/dependabot.yaml", "keep"))
        unreadable = fb._plan_dependabot(_facts([".github/dependabot.yml", "go.mod"]), _reader({}))
        self.assertEqual(unreadable.action, "gap")


class ApplyAndIoTests(unittest.TestCase):
    """Writing, git listing and reading."""

    def test_apply_actions_writes_lf_and_skips_non_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            written = fb.apply_actions(
                root,
                [
                    fb.FileAction("a/b.md", "create", "missing", "one\ntwo\n"),
                    fb.FileAction("keep.md", "keep", "repository-authored file"),
                    fb.FileAction("odd.md", "create", "no content"),
                ],
            )
            self.assertEqual(written, ["a/b.md"])
            self.assertEqual((root / "a" / "b.md").read_bytes(), b"one\ntwo\n")
            self.assertFalse((root / "odd.md").exists())

    def test_list_tracked_files_uses_runner(self) -> None:
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout="a.py\0b/c.md\0", stderr="")

        self.assertEqual(fb.list_tracked_files(Path("repo"), runner=runner), ("a.py", "b/c.md"))
        self.assertEqual(calls[0][0][:2], ["git", "-C"])
        self.assertEqual(calls[0][0][-2:], ["ls-files", "-z"])
        self.assertTrue(calls[0][1]["check"])

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
            code, out, _err = self._run(base + ["--write", "--private", "--default-branch", "trunk"], ["app.py"])
            self.assertEqual(code, 0)
            self.assertIn("wrote SECURITY.md", out)
            self.assertIn("`trunk`", (root / "SECURITY.md").read_text(encoding="utf-8"))
            tracked = [
                "app.py",
                "SECURITY.md",
                "CONTRIBUTING.md",
                "CODE_OF_CONDUCT.md",
                ".github/PULL_REQUEST_TEMPLATE.md",
                ".github/ISSUE_TEMPLATE/config.yml",
                ".github/dependabot.yml",
                fb.QUALITY_CALLER,
            ]
            dependabot = root / ".github" / "dependabot.yml"
            dependabot.write_text(fb.render_dependabot({"github-actions": ["/"]}), encoding="utf-8")
            code, out, _err = self._run(base, tracked)
            self.assertEqual(code, 0, out)
            dependabot.write_text("version: 2\nupdates: []\n", encoding="utf-8")
            code, out, _err = self._run(base + ["--write"], tracked)
            self.assertEqual(code, 1)
            self.assertIn("gap     .github/dependabot.yml", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
