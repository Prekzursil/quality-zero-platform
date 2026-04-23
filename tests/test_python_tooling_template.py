"""Render tests for ``profiles/templates/stack/python-tooling/ci.yml.j2``.

Phase 3 of ``docs/QZP-V2-DESIGN.md`` §4: python-tooling targets
CLI tools + library packages (including the platform itself).
Adds a ``lint`` job to the python-only base contract — ruff + lizard.
"""

from __future__ import absolute_import

import unittest

import yaml  # type: ignore[import-untyped]

from scripts.quality import template_render as tr


class PythonToolingCiTemplateTests(unittest.TestCase):
    """``stack/python-tooling/ci.yml.j2`` renders test + lint jobs."""

    @staticmethod
    def _render(profile: dict) -> str:
        """Render the template against ``profile``."""
        return tr.render_template("stack/python-tooling/ci.yml.j2", profile)

    def test_renders_test_and_lint_jobs(self) -> None:
        """Both ``test`` and ``lint`` jobs are present in the rendered workflow."""
        rendered = self._render({
            "default_branch": "main",
            "coverage": {"command": "pytest --cov"},
        })
        doc = yaml.safe_load(rendered)
        self.assertEqual(doc["name"], "Python Tooling CI")
        self.assertIn("test", doc["jobs"])
        self.assertIn("lint", doc["jobs"])

    def test_lint_job_installs_ruff_and_lizard(self) -> None:
        """The lint job pulls in the exact CLI tools the platform uses."""
        rendered = self._render({"coverage": {"command": "pytest"}})
        self.assertIn("pip install ruff lizard", rendered)
        self.assertIn("ruff check scripts/ tests/", rendered)
        # Lizard's complexity ceiling defaults to 15, matching the
        # platform's own ``lizard src/ -C 15`` gate.
        self.assertIn("lizard scripts/ -C 15", rendered)

    def test_default_coverage_command_includes_branch_flag(self) -> None:
        """Default command produces branch coverage so the 100% gate works."""
        rendered = self._render({"coverage": {}})
        self.assertIn("pytest --cov --cov-branch --cov-report=xml", rendered)

    def test_concurrency_group_is_stack_specific(self) -> None:
        """Concurrency group name distinguishes python-tooling from python-only."""
        rendered = self._render({"coverage": {"command": "pytest"}})
        self.assertIn("group: python-tooling-ci-${{ github.ref }}", rendered)

    def test_list_templates_includes_python_tooling_ci(self) -> None:
        """``list_templates('python-tooling')`` surfaces the new file."""
        mapping = tr.list_templates("python-tooling")
        self.assertIn("stack/python-tooling/ci.yml.j2", mapping)
        self.assertEqual(mapping["stack/python-tooling/ci.yml.j2"], "ci.yml")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
