"""Render tests for ``profiles/templates/stack/react-vite-vitest/ci.yml.j2``.

Phase 3 of ``docs/QZP-V2-DESIGN.md`` §4: react-vite-vitest is the
frontend-only complement to python-only (vitest + v8 + eslint +
prettier + tsc).
"""

from __future__ import absolute_import

import unittest

import yaml  # type: ignore[import-untyped]

from scripts.quality import template_render as tr


class ReactViteVitestCiTemplateTests(unittest.TestCase):
    """``stack/react-vite-vitest/ci.yml.j2`` renders the frontend workflow."""

    @staticmethod
    def _render(profile: dict) -> str:
        """Render against ``profile``."""
        return tr.render_template(
            "stack/react-vite-vitest/ci.yml.j2", profile
        )

    def test_workflow_has_all_frontend_gates(self) -> None:
        """Test + lint + prettier + tsc gates all render in the single job."""
        rendered = self._render({
            "default_branch": "main",
            "coverage": {"setup": {"node": "20"}, "command": "npx vitest run --coverage"},
        })
        doc = yaml.safe_load(rendered)
        self.assertEqual(doc["name"], "React Vite Vitest CI")
        self.assertIn("test", doc["jobs"])
        rendered_lower = rendered.lower()
        self.assertIn("tsc --noemit", rendered_lower)
        self.assertIn("eslint .", rendered_lower)
        self.assertIn("prettier --check", rendered_lower)
        self.assertIn("vitest run --coverage", rendered_lower)

    def test_setup_node_fragment_renders_inside_job(self) -> None:
        """``coverage.setup.node`` propagates into setup-node@v4."""
        rendered = self._render({"coverage": {"setup": {"node": "24"}}})
        self.assertIn("actions/setup-node@v4", rendered)
        self.assertIn('node-version: "24"', rendered)

    def test_concurrency_group_distinguishes_from_python(self) -> None:
        """Each stack has its own concurrency group name."""
        rendered = self._render({"coverage": {}})
        self.assertIn(
            "group: react-vite-vitest-ci-${{ github.ref }}", rendered
        )

    def test_default_branch_override(self) -> None:
        """Repos on non-``main`` default branches honour the profile field."""
        rendered = self._render({
            "default_branch": "trunk",
            "coverage": {"setup": {"node": "20"}},
        })
        doc = yaml.safe_load(rendered)
        self.assertEqual(doc[True]["push"]["branches"], ["trunk"])

    def test_list_templates_surfaces_react_vite_vitest_ci(self) -> None:
        """``list_templates('react-vite-vitest')`` finds the template."""
        mapping = tr.list_templates("react-vite-vitest")
        self.assertIn(
            "stack/react-vite-vitest/ci.yml.j2", mapping
        )
        self.assertEqual(
            mapping["stack/react-vite-vitest/ci.yml.j2"], "ci.yml"
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
