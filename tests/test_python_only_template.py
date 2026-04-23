"""Render tests for ``profiles/templates/stack/python-only/ci.yml.j2``.

Phase 3 of ``docs/QZP-V2-DESIGN.md`` ships per-stack workflow templates
the drift-sync pipeline renders into consumer repos. This suite pins
the python-only stack's ``ci.yml`` contract.
"""

from __future__ import absolute_import

import unittest

import yaml  # type: ignore[import-untyped]

from scripts.quality import template_render as tr


class PythonOnlyCiTemplateTests(unittest.TestCase):
    """``stack/python-only/ci.yml.j2`` renders a valid GitHub Actions workflow."""

    @staticmethod
    def _render(profile: dict) -> str:
        """Render the template against ``profile``."""
        return tr.render_template("stack/python-only/ci.yml.j2", profile)

    def test_default_profile_renders_valid_workflow(self) -> None:
        """A minimal profile produces parseable YAML with ``test`` job."""
        rendered = self._render({
            "default_branch": "main",
            "coverage": {"command": "pytest --cov"},
        })
        doc = yaml.safe_load(rendered)
        self.assertEqual(doc["name"], "Python CI")
        self.assertIn("test", doc["jobs"])
        self.assertEqual(doc["jobs"]["test"]["runs-on"], "ubuntu-latest")
        # ``on:`` is parsed as YAML boolean True; GitHub Actions accepts
        # the unquoted key, so the tests look it up via the bool key.
        on_block = doc[True]
        self.assertEqual(on_block["push"]["branches"], ["main"])
        self.assertEqual(on_block["pull_request"]["branches"], ["main"])

    def test_default_branch_falls_back_to_main(self) -> None:
        """Omitting ``default_branch`` still yields ``main``."""
        rendered = self._render({"coverage": {"command": "pytest"}})
        doc = yaml.safe_load(rendered)
        self.assertEqual(doc[True]["push"]["branches"], ["main"])

    def test_custom_python_version_propagates(self) -> None:
        """``coverage.setup.python`` reaches the rendered setup-python step."""
        rendered = self._render({
            "coverage": {
                "setup": {"python": "3.13"},
                "command": "pytest",
            },
        })
        self.assertIn('python-version: "3.13"', rendered)

    def test_pinned_checkout_action(self) -> None:
        """``actions/checkout@v4`` with ``persist-credentials: false`` is pinned."""
        rendered = self._render({"coverage": {"command": "pytest"}})
        self.assertIn("actions/checkout@v4", rendered)
        self.assertIn("persist-credentials: false", rendered)

    def test_concurrency_group_uses_github_ref(self) -> None:
        """Concurrency group interpolates ``github.ref`` to dedupe runs per branch."""
        rendered = self._render({"coverage": {"command": "pytest"}})
        self.assertIn("concurrency:", rendered)
        self.assertIn("group: python-ci-${{ github.ref }}", rendered)

    def test_missing_coverage_command_falls_back_to_pytest_cov(self) -> None:
        """No declared ``coverage.command`` yields the generic ``pytest --cov``."""
        rendered = self._render({"coverage": {}})
        self.assertIn("pytest --cov", rendered)

    def test_list_templates_includes_python_only_ci(self) -> None:
        """``list_templates('python-only')`` surfaces the new file."""
        mapping = tr.list_templates("python-only")
        self.assertIn("stack/python-only/ci.yml.j2", mapping)
        self.assertEqual(mapping["stack/python-only/ci.yml.j2"], "ci.yml")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
