"""Tests for the gate-3 JS/TS project planner (``scripts/quality/jsts_projects.py``).

The jsts coverage lane used to measure only the root project whenever a root
``package.json`` existed, so nested projects were never measured and a nested
project with tests but no coverage script was never flagged. The planner is the
fix's single source of truth: it is embedded verbatim into
``reusable-quality.yml`` (byte-identity is asserted by
``tests/test_lean_gate_embedded_helpers.py``), so its behaviour is pinned here.
"""

from __future__ import absolute_import

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.quality import jsts_projects as planner


class ProjectDirsTests(unittest.TestCase):
    """Every first-party package.json is a project; vendored trees never are."""

    def test_root_and_nested_projects_root_first(self) -> None:
        """The root sorts first so the lane can measure it before its members."""
        paths = ["web/package.json", "package.json", "api/package.json", "README.md"]
        self.assertEqual(planner.project_dirs(paths), [".", "api", "web"])

    def test_vendored_and_build_trees_are_not_projects(self) -> None:
        """node_modules / dist / build / vendor package.json files are ignored."""
        paths = [
            "node_modules/x/package.json",
            "web/node_modules/y/package.json",
            "dist/package.json",
            "vendor/lib/package.json",
            "web/package.json",
        ]
        self.assertEqual(planner.project_dirs(paths), ["web"])

    def test_no_package_json_means_no_projects(self) -> None:
        """A tree without any package.json yields an empty plan."""
        self.assertEqual(planner.project_dirs(["src/a.ts", "package-lock.json"]), [])


class OwningProjectTests(unittest.TestCase):
    """A test file belongs to its DEEPEST enclosing project."""

    def test_deepest_project_wins(self) -> None:
        """A nested project beats both the root and its own parent project."""
        dirs = [".", "apps", "apps/web"]
        self.assertEqual(planner.owning_project("apps/web/src/a.test.ts", dirs), "apps/web")
        self.assertEqual(planner.owning_project("apps/b.test.ts", dirs), "apps")
        self.assertEqual(planner.owning_project("src/c.test.ts", dirs), ".")

    def test_root_never_displaces_a_nested_owner(self) -> None:
        """Order of the candidate list does not matter."""
        self.assertEqual(planner.owning_project("web/a.test.ts", ["web", "."]), "web")

    def test_sibling_prefix_is_not_containment(self) -> None:
        """``web2/`` is not inside ``web``."""
        self.assertIsNone(planner.owning_project("web2/a.test.ts", ["web"]))

    def test_no_projects_means_no_owner(self) -> None:
        """Without projects every test is an orphan."""
        self.assertIsNone(planner.owning_project("a.test.ts", []))


class WorkspaceGlobTests(unittest.TestCase):
    """Workspace declarations come from package.json and pnpm-workspace.yaml."""

    def test_npm_workspaces_list(self) -> None:
        """The npm / yarn list form, blanks and non-strings dropped."""
        text = json.dumps({"workspaces": ["packages/*", "", 7, "apps/web"]})
        self.assertEqual(planner.workspace_globs(text), ["packages/*", "apps/web"])

    def test_yarn_classic_workspaces_object(self) -> None:
        """Yarn classic nests the list under ``packages``."""
        text = json.dumps({"workspaces": {"packages": ["libs/*"], "nohoist": ["**/x"]}})
        self.assertEqual(planner.workspace_globs(text), ["libs/*"])

    def test_no_or_malformed_workspaces_yield_nothing(self) -> None:
        """Missing, non-list, non-object or invalid JSON all mean no workspace."""
        self.assertEqual(planner.workspace_globs(json.dumps({"name": "x"})), [])
        self.assertEqual(planner.workspace_globs(json.dumps({"workspaces": "packages/*"})), [])
        self.assertEqual(planner.workspace_globs(json.dumps(["not", "an", "object"])), [])
        self.assertEqual(planner.workspace_globs("{not json"), [])
        self.assertEqual(planner.workspace_globs(), [])

    def test_pnpm_workspace_packages(self) -> None:
        """Only the items under the top-level ``packages:`` key are globs."""
        text = "\n".join(
            [
                "# workspace",
                "packages:",
                "  - 'packages/*'   # every package",
                '  - "!**/fixtures/**"',
                "  -",
                "",
                "catalog:",
                "  - not-a-glob",
                "- top-level-item-outside-packages",
            ]
        )
        self.assertEqual(planner.workspace_globs(pnpm_workspace=text), ["packages/*", "!**/fixtures/**"])

    def test_both_sources_are_combined(self) -> None:
        """A repo may declare both; the union applies."""
        globs = planner.workspace_globs(
            json.dumps({"workspaces": ["a/*"]}),
            "packages:\n- b/*\n",
        )
        self.assertEqual(globs, ["a/*", "b/*"])


class WorkspaceMembershipTests(unittest.TestCase):
    """Membership honours includes, ``!`` exclusions and path spellings."""

    def test_root_is_never_a_member(self) -> None:
        """The root is the workspace, not a member of it."""
        self.assertFalse(planner.is_workspace_member(".", ["*"]))

    def test_include_glob_matches(self) -> None:
        """``./`` prefixes and trailing slashes are normalised."""
        self.assertTrue(planner.is_workspace_member("packages/ui", ["./packages/*/"]))

    def test_exclusion_wins_regardless_of_order(self) -> None:
        """A matching ``!`` glob removes the directory even if listed first."""
        self.assertFalse(planner.is_workspace_member("packages/legacy", ["!packages/legacy", "packages/*"]))
        self.assertFalse(planner.is_workspace_member("packages/legacy", ["packages/*", "!./packages/legacy"]))

    def test_non_matching_exclusion_is_ignored(self) -> None:
        """An exclusion for another directory does not affect this one."""
        self.assertTrue(planner.is_workspace_member("packages/ui", ["packages/*", "!packages/legacy"]))

    def test_unlisted_directory_is_not_a_member(self) -> None:
        """No matching include glob means not a member."""
        self.assertFalse(planner.is_workspace_member("tools/cli", ["packages/*"]))

    def test_single_star_stays_inside_one_segment(self) -> None:
        """``packages/*`` names direct children only, as npm and pnpm do.

        Plain fnmatch let ``*`` cross ``/``, so a project nested INSIDE a
        member was claimed as a member and passed as "measured by the root".
        """
        self.assertTrue(planner.glob_match("packages/ui", "packages/*"))
        self.assertFalse(planner.glob_match("packages/ui/legacy", "packages/*"))
        self.assertFalse(planner.is_workspace_member("packages/ui/legacy", ["packages/*"]))

    def test_double_star_spans_any_number_of_segments(self) -> None:
        """``**`` matches zero or more whole segments."""
        self.assertTrue(planner.glob_match("packages/ui/legacy", "packages/**"))
        self.assertTrue(planner.glob_match("packages", "packages/**"))
        self.assertTrue(planner.glob_match("a/fixtures/b", "**/fixtures/**"))
        self.assertTrue(planner.glob_match("apps/web/app", "apps/*/app"))
        self.assertFalse(planner.glob_match("apps/web/lib", "apps/*/app"))
        self.assertFalse(planner.glob_match("apps", "apps/*"))


class PlanTests(unittest.TestCase):
    """The plan attributes tests and flags members."""

    def test_nested_projects_are_planned_even_with_a_root_package_json(self) -> None:
        """THE hole: a root package.json used to hide every nested project."""
        paths = [
            "package.json",
            "src/app.ts",
            "frontend/package.json",
            "frontend/src/app.test.tsx",
            "packages/ui/package.json",
            "packages/ui/__tests__/button.js",
            "tools/package.json",
            "tools/build.mjs",
            "node_modules/dep/index.test.js",
            "web/legacy.spec.cjs",
            "e2e/flow.spec.ts",
        ]
        rows, orphans = planner.plan(
            paths,
            root_package_json=json.dumps({"workspaces": ["packages/*"]}),
        )
        self.assertEqual(
            rows,
            [
                (".", True, False),
                ("frontend", True, False),
                ("packages/ui", True, True),
                ("tools", False, False),
            ],
        )
        self.assertEqual(orphans, [])

    def test_tests_outside_every_project_are_orphans(self) -> None:
        """With no root package.json, a stray test belongs to nobody."""
        rows, orphans = planner.plan(["web/package.json", "scripts/x.test.mjs", "docs/readme.md"])
        self.assertEqual(rows, [("web", False, False)])
        self.assertEqual(orphans, ["scripts/x.test.mjs"])

    def test_member_tests_stay_with_the_member(self) -> None:
        """An aggregator root does not inherit its members' tests; the lane decides if it must run."""
        rows, _ = planner.plan(
            ["package.json", "packages/ui/package.json", "packages/ui/a.test.ts"],
            root_package_json=json.dumps({"workspaces": ["packages/*"]}),
        )
        self.assertEqual(rows, [(".", False, False), ("packages/ui", True, True)])

    def test_mocha_style_test_directories_count_inside_a_project(self) -> None:
        """THE review finding: `test/smoke.js` and `tests/unit.ts` are tests of their project."""
        rows, orphans = planner.plan(
            [
                "package.json",
                "test/smoke.js",
                "api/package.json",
                "api/tests/unit.ts",
                "web/package.json",
                "web/src/contest/x.ts",
                "web/src/tests.ts",
            ]
        )
        self.assertEqual(rows, [(".", True, False), ("api", True, False), ("web", False, False)])
        self.assertEqual(orphans, [])

    def test_cypress_specs_are_tests_and_can_be_orphans(self) -> None:
        """`*.cy.*` is the Cypress convention: a project test inside one, an orphan outside all."""
        rows, orphans = planner.plan(["web/package.json", "web/cypress/e2e/login.cy.ts", "e2e/flow.cy.tsx"])
        self.assertEqual(rows, [("web", True, False)])
        self.assertEqual(orphans, ["e2e/flow.cy.tsx"])

    def test_fixture_trees_are_neither_projects_nor_tests(self) -> None:
        """A package.json or a `*.test.*` under fixtures/ / __mocks__ / testdata is test input."""
        paths = [
            "package.json",
            "tests/fixtures/sample/package.json",
            "tests/fixtures/sample/index.js",
            "test/__fixtures__/x.test.js",
            "src/__mocks__/fs.js",
            "testdata/pkg/package.json",
        ]
        self.assertEqual(planner.project_dirs(paths), ["."])
        rows, orphans = planner.plan(paths)
        self.assertEqual(rows, [(".", False, False)])
        self.assertEqual(orphans, [])

    def test_a_test_directory_outside_every_project_is_not_an_orphan(self) -> None:
        """Outside a JS/TS project, `tests/` says nothing about JavaScript tests; named tests still orphan."""
        rows, orphans = planner.plan(["web/package.json", "tests/helpers/data.js", "e2e/flow.spec.ts"])
        self.assertEqual(rows, [("web", False, False)])
        self.assertEqual(orphans, ["e2e/flow.spec.ts"])

    def test_render_is_tab_separated_yes_no(self) -> None:
        """The lane splits on tabs and compares to yes / no."""
        rendered = planner.render([(".", True, False), ("web app", False, True)])
        self.assertEqual(rendered, ".\tyes\tno\nweb app\tno\tyes\n")


class MainTests(unittest.TestCase):
    """The CLI the lane invokes."""

    def setUp(self) -> None:
        """Give each test a scratch directory."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _write(self, name: str, text: str) -> str:
        """Write ``text`` to a scratch file and return its path."""
        path = self.tmp / name
        path.write_text(text, encoding="utf-8", newline="\n")
        return str(path)

    def test_main_prints_the_plan_and_warns_about_orphans(self) -> None:
        """stdout carries the plan; stderr names orphan tests."""
        files = self._write(
            "files.txt",
            "package.json\npackages/a/package.json\npackages/a/a.test.ts\n\nloose/b.test.ts\nweb/package.json\n",
        )
        root = self._write("package.json", json.dumps({"workspaces": ["packages/*"]}))
        pnpm = self._write("pnpm-workspace.yaml", "packages:\n  - web\n")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = planner.main(["--files", files, "--root-package-json", root, "--pnpm-workspace", pnpm])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), ".\tyes\tno\npackages/a\tyes\tyes\nweb\tno\tyes\n")
        self.assertEqual(err.getvalue(), "")

    def test_main_without_optional_inputs_reports_orphans(self) -> None:
        """No root package.json: the stray test is reported, the plan still prints."""
        files = self._write("files.txt", "web/package.json\nloose/b.test.ts\n")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = planner.main(["--files", files])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "web\tno\tno\n")
        self.assertIn("loose/b.test.ts", err.getvalue())

    def test_main_writes_orphans_and_reads_nul_separated_input(self) -> None:
        """`git ls-files -z` output plans the same; orphans land in --orphans-out."""
        files = self._write("files.txt", "web/package.json\0web/a.test.ts\0loose/b.test.ts\0")
        orphans = self.tmp / "orphans.txt"
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = planner.main(["--files", files, "--orphans-out", str(orphans)])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "web\tyes\tno\n")
        self.assertEqual(orphans.read_text(encoding="utf-8"), "loose/b.test.ts\n")

    def test_main_without_files_is_a_usage_error(self) -> None:
        """Planning needs the file list."""
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(planner.main([]), 2)
        self.assertIn("--files is required", err.getvalue())

    def test_main_fails_closed_on_unreadable_input(self) -> None:
        """A missing file list is exit 2, never an empty (all-clear) plan."""
        err = io.StringIO()
        with redirect_stderr(err):
            code = planner.main(["--files", str(self.tmp / "missing.txt")])
        self.assertEqual(code, 2)
        self.assertIn("cannot read the plan inputs", err.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
