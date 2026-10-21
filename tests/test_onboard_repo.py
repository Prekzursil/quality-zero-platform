"""Coverage for ``scripts/quality/onboard_repo.py``.

The gh layer (metadata fetch, check-run measurement, shallow clone) runs
through an injectable scripted runner; stack detection is exercised against
real temp-directory file layouts.
"""

from __future__ import absolute_import

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, List, Sequence

from scripts.quality import onboard_repo as ob


def _completed(
    args: Sequence[str], *, returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    """Build a CompletedProcess for the scripted runner."""
    return subprocess.CompletedProcess(args=list(args), returncode=returncode, stdout=stdout, stderr=stderr)


class DispatchRunner:
    """Runner that answers by inspecting the gh argv shape."""

    def __init__(
        self, *, branch: str = "main", pages: Any = None, fail_clone: bool = False, fail_meta: bool = False
    ) -> None:
        """Configure the scripted responses."""
        self.branch = branch
        self.pages = (
            pages if pages is not None else [{"check_runs": [{"name": "quality / quality", "conclusion": "success"}]}]
        )
        self.fail_clone = fail_clone
        self.fail_meta = fail_meta
        self.calls: List[Sequence[str]] = []

    def __call__(self, args: Sequence[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Answer repos/<slug>, clone, and check-runs calls."""
        self.calls.append(list(args))
        argv = list(args)
        if argv[1] == "api" and ".default_branch" in argv:
            if self.fail_meta:
                return _completed(argv, returncode=1, stderr="gh: Not Found")
            return _completed(argv, stdout=self.branch)
        if argv[1] == "repo" and argv[2] == "clone":
            # argv: gh repo clone <slug> <dest> -- --depth 1
            dest = Path(argv[4])
            if self.fail_clone:
                return _completed(argv, returncode=1, stderr="clone failed")
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
            return _completed(argv)
        if argv[1] == "api":
            if "check-runs" in " ".join(argv):
                return _completed(argv, stdout=json.dumps(self.pages))
            return _completed(argv, returncode=1, stderr="gh: Not Found")
        return _completed(argv, returncode=1, stderr="unexpected gh call")


class DetectStackTests(unittest.TestCase):
    """File-signature stack detection."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _touch(self, name: str, content: str = "") -> None:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_go(self) -> None:
        self._touch("go.mod", "module x\n")
        self.assertEqual(ob.detect_stack(self.dir), ("go", "go.mod in the repo root"))

    def test_cmake(self) -> None:
        self._touch("CMakeLists.txt", "cmake_minimum_required(VERSION 3.10)\n")
        self.assertEqual(ob.detect_stack(self.dir)[0], "cpp-cmake")

    def test_cpp_sources(self) -> None:
        self._touch("main.cpp", "int main() { return 0; }\n")
        self.assertEqual(ob.detect_stack(self.dir)[0], "cpp-cmake")

    def test_dotnet(self) -> None:
        self._touch("App.csproj", '<Project Sdk="Microsoft.NET.Sdk" />\n')
        self.assertEqual(ob.detect_stack(self.dir)[0], "dotnet-wpf")

    def test_gradle(self) -> None:
        self._touch("build.gradle.kts", "plugins {}\n")
        self.assertEqual(ob.detect_stack(self.dir)[0], "gradle-java")

    def test_fullstack(self) -> None:
        self._touch("package.json", "{}\n")
        self._touch("requirements.txt", "flask\n")
        self.assertEqual(ob.detect_stack(self.dir)[0], "fullstack-web")

    def test_node_frontend(self) -> None:
        self._touch("package.json", "{}\n")
        self.assertEqual(ob.detect_stack(self.dir)[0], "node-frontend")

    def test_python_web(self) -> None:
        self._touch("pyproject.toml", "dependencies = ['fastapi']\n")
        self.assertEqual(ob.detect_stack(self.dir)[0], "python-web")

    def test_python_desktop(self) -> None:
        self._touch("requirements.txt", "PySide6\n")
        self.assertEqual(ob.detect_stack(self.dir)[0], "python-desktop")

    def test_python_tooling(self) -> None:
        self._touch("pyproject.toml", "[project]\nname = 'x'\n")
        self.assertEqual(ob.detect_stack(self.dir)[0], "python-tooling")

    def test_unknown(self) -> None:
        self._touch("README.md", "nothing here\n")
        stack, rationale = ob.detect_stack(self.dir)
        self.assertEqual(stack, "")
        self.assertIn("no known stack signature", rationale)


class PureHelpersTests(unittest.TestCase):
    """Derivation helpers: required set, codeql, coverage, dependabot, render."""

    def test_profile_name_for_slug(self) -> None:
        self.assertEqual(ob.profile_name_for_slug("Prekzursil/Fractal-Darkroom"), "fractal-darkroom")

    def test_derive_codeql_languages(self) -> None:
        self.assertEqual(
            ob.derive_codeql_languages(
                ["Analyze (python)", "Analyze (actions)", "Analyze (python)", "quality / quality", "Analyze ()"]
            ),
            ["python", "actions"],
        )

    def test_derive_required_without_codeql(self) -> None:
        required, note = ob.derive_required_contexts(["quality / quality", "test (ubuntu-latest, 3.12)"])
        self.assertEqual(required, ["quality / quality", "test (ubuntu-latest, 3.12)"])
        self.assertNotIn("CodeQL", note)

    def test_derive_required_with_codeql(self) -> None:
        required, note = ob.derive_required_contexts(["quality / quality", "Analyze (python)"])
        self.assertEqual(required, ["quality / quality", "CodeQL"])
        self.assertIn("PR-only", note)

    def test_coverage_for_stack_python_with_pytest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
            cov = ob.coverage_for_stack("python-tooling", Path(tmp))
        self.assertIn("pytest", cov["command"])
        self.assertEqual(cov["inputs"][0]["format"], "xml")

    def test_coverage_for_stack_python_without_pytest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cov = ob.coverage_for_stack("python-tooling", Path(tmp))
        self.assertIn("coverage run -m unittest", cov["command"])

    def test_coverage_for_stack_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cov = ob.coverage_for_stack("node-frontend", Path(tmp))
        self.assertIn("npm run test:coverage", cov["command"])
        self.assertEqual(cov["inputs"][0]["format"], "lcov")

    def test_coverage_for_stack_other(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cov = ob.coverage_for_stack("go", Path(tmp))
        self.assertEqual(cov["inputs"], [])
        self.assertIn("TODO", cov["command"])

    def test_verify_command_for_stack(self) -> None:
        self.assertEqual(ob.verify_command_for_stack("python-tooling"), "python -m pytest")
        self.assertEqual(ob.verify_command_for_stack("fullstack-web"), "python -m pytest")
        self.assertEqual(ob.verify_command_for_stack("node-frontend"), "npm test")
        self.assertEqual(ob.verify_command_for_stack("go"), "go test ./...")
        self.assertIn("TODO", ob.verify_command_for_stack("cpp-cmake"))

    def test_dependabot_for_stack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "requirements.txt").write_text("flask\n", encoding="utf-8")
            rows = ob.dependabot_for_stack("python-web", Path(tmp))
        self.assertEqual([row["ecosystem"] for row in rows], ["pip"])

    def test_dependabot_for_stack_node_and_go(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "package.json").write_text("{}\n", encoding="utf-8")
            Path(tmp, "go.mod").write_text("module x\n", encoding="utf-8")
            rows = ob.dependabot_for_stack("fullstack-web", Path(tmp))
        self.assertEqual([row["ecosystem"] for row in rows], ["npm", "gomod"])

    def test_dependabot_for_stack_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ob.dependabot_for_stack("cpp-cmake", Path(tmp)), [])

    def test_render_profile_rich_and_sparse(self) -> None:
        rich = ob.render_profile(
            "x/y",
            "python-tooling",
            ["quality / quality", "CodeQL"],
            ["python"],
            {
                "command": "python -m pytest --cov\n",
                "inputs": [{"format": "xml", "name": "c", "path": "coverage.xml", "flag": "c"}],
            },
            "python -m pytest",
            [{"ecosystem": "pip", "directory": "/"}],
            "note here",
            "main",
        )
        for needle in (
            "version: 2",
            "phase: shadow",
            "    - quality / quality",
            "    - CodeQL",
            "      flag: c",
            "    - python",
            "ecosystem: pip",
        ):
            self.assertIn(needle, rich)
        sparse = ob.render_profile(
            "x/y",
            "go",
            [],
            [],
            {"command": "# TODO\n", "inputs": []},
            "go test ./...",
            [],
            "note",
            "main",
        )
        self.assertIn("    [] # TODO: set languages if CodeQL runs on this repo", sparse)
        self.assertIn("    [] # TODO: add ecosystems for the repo's manifests", sparse)
        self.assertIn("  always:\n  pull_request_only: []\n  target:\n", sparse)

    def test_render_inventory_entry(self) -> None:
        entry = ob.render_inventory_entry("x/y", "y", "main", "python-tooling")
        self.assertIn("slug: x/y", entry)
        self.assertIn("rollout: observe", entry)
        self.assertIn("stack python-tooling", entry)

    def test_next_commands_dry_run_and_write(self) -> None:
        dry = ob.next_commands(
            profile_name="y",
            profile_path=Path("/tmp/x.yml"),
            inventory_fragment="  - slug: x/y\n",
            dry_run=True,
            temp_dir=Path("/tmp/out"),
        )
        self.assertTrue(any("cp " in line for line in dry))
        wet = ob.next_commands(
            profile_name="y",
            profile_path=Path("/tmp/x.yml"),
            inventory_fragment="  - slug: x/y\n",
            dry_run=False,
            temp_dir=None,
        )
        self.assertTrue(any("profile written to" in line for line in wet))


class GhHelperTests(unittest.TestCase):
    """_resolve_default_branch + _shallow_clone failure modes."""

    def test_branch_ok(self) -> None:
        self.assertEqual(ob._resolve_default_branch("x/y", runner=DispatchRunner(branch="trunk")), "trunk")

    def test_branch_failure_raises(self) -> None:
        with self.assertRaises(SystemExit):
            ob._resolve_default_branch("x/y", runner=DispatchRunner(fail_meta=True))

    def test_branch_empty_raises(self) -> None:
        def empty_branch(args: Sequence[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return _completed(list(args), stdout="\n")

        with self.assertRaises(SystemExit):
            ob._resolve_default_branch("x/y", runner=empty_branch)

    def test_clone_failure_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit):
            ob._shallow_clone("x/y", Path(tmp) / "repo", runner=DispatchRunner(fail_clone=True))


class MainTests(unittest.TestCase):
    """CLI end-to-end with a scripted gh + temp workspaces."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.source = self.dir / "consumer"
        self.source.mkdir()
        (self.source / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        self.root = self.dir / "platform"
        (self.root / "profiles" / "repos").mkdir(parents=True)
        (self.root / "inventory").mkdir(parents=True)
        (self.root / "inventory" / "repos.yml").write_text("version: 1\nrepos: []\n", encoding="utf-8")

    def _run(self, argv: List[str], runner: Callable[..., subprocess.CompletedProcess[str]]) -> int:
        return ob.main(argv, root=self.root, runner=runner)

    def test_dry_run_emits_skeleton_and_validator_output(self) -> None:
        code = self._run(
            ["--slug", "Prekzursil/demo", "--source-dir", str(self.source), "--branch", "main", "--dry-run"],
            DispatchRunner(),
        )
        self.assertEqual(code, 0)
        self.assertFalse((self.root / "profiles" / "repos" / "demo.yml").exists())
        self.assertEqual((self.root / "inventory" / "repos.yml").read_text(encoding="utf-8"), "version: 1\nrepos: []\n")

    def test_write_mode_creates_profile_and_appends_inventory(self) -> None:
        code = self._run(
            ["--slug", "Prekzursil/demo", "--source-dir", str(self.source), "--branch", "main"],
            DispatchRunner(),
        )
        self.assertEqual(code, 0)
        profile = self.root / "profiles" / "repos" / "demo.yml"
        self.assertTrue(profile.is_file())
        text = profile.read_text(encoding="utf-8")
        self.assertIn("phase: shadow", text)
        self.assertIn("quality / quality", text)
        self.assertIn("slug: Prekzursil/demo", (self.root / "inventory" / "repos.yml").read_text(encoding="utf-8"))

    def test_write_mode_appends_newline_before_fragment_when_missing(self) -> None:
        (self.root / "inventory" / "repos.yml").write_text("version: 1\nrepos: []", encoding="utf-8")  # no trailing \n
        code = self._run(
            ["--slug", "Prekzursil/demo", "--source-dir", str(self.source), "--branch", "main"],
            DispatchRunner(),
        )
        self.assertEqual(code, 0)
        text = (self.root / "inventory" / "repos.yml").read_text(encoding="utf-8")
        self.assertIn("repos: []\n  - slug: Prekzursil/demo", text)

    def test_write_mode_refuses_existing_profile(self) -> None:
        (self.root / "profiles" / "repos" / "demo.yml").write_text("slug: Prekzursil/demo\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self._run(
                ["--slug", "Prekzursil/demo", "--source-dir", str(self.source), "--branch", "main"],
                DispatchRunner(),
            )

    def test_measure_failure_exits(self) -> None:
        with self.assertRaises(SystemExit):
            self._run(
                ["--slug", "Prekzursil/demo", "--source-dir", str(self.source), "--branch", "main", "--dry-run"],
                _FailingMeasureRunner(),
            )

    def test_unknown_stack_exits(self) -> None:
        empty = self.dir / "empty"
        empty.mkdir()
        with self.assertRaises(SystemExit):
            self._run(
                ["--slug", "Prekzursil/demo", "--source-dir", str(empty), "--branch", "main", "--dry-run"],
                DispatchRunner(),
            )

    def test_clone_path_used_when_no_source_dir(self) -> None:
        runner = DispatchRunner()
        code = self._run(["--slug", "Prekzursil/demo", "--branch", "main", "--dry-run"], runner)
        self.assertEqual(code, 0)
        self.assertTrue(any(call[1] == "repo" and call[2] == "clone" for call in runner.calls))
        # the temp clone tree is cleaned up afterwards (dest is argv[4])
        leftovers = [call for call in runner.calls if call[1] == "repo" and Path(call[4]).exists()]
        self.assertEqual(leftovers, [])

    def test_default_branch_fetched_when_missing(self) -> None:
        runner = DispatchRunner(branch="trunk")
        code = self._run(["--slug", "Prekzursil/demo", "--source-dir", str(self.source), "--dry-run"], runner)
        self.assertEqual(code, 0)
        self.assertTrue(any(".default_branch" in " ".join(call) for call in runner.calls))


class _FailingMeasureRunner(DispatchRunner):
    """Runner whose check-runs call fails (repo unmeasurable)."""

    def __call__(self, args: Sequence[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        argv = list(args)
        if argv[1] == "api" and "check-runs" in " ".join(argv):
            return _completed(argv, returncode=1, stderr="gh: Not Found")
        return super().__call__(args, **_kwargs)


if __name__ == "__main__":
    unittest.main()
