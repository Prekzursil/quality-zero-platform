"""Render tests for ``profiles/templates/stack/go/ci.yml.j2``."""

from __future__ import absolute_import

import unittest

import yaml  # type: ignore[import-untyped]

from scripts.quality import template_render as tr


class GoCiTemplateTests(unittest.TestCase):
    """``stack/go/ci.yml.j2`` renders a Go CI workflow."""

    @staticmethod
    def _render(profile: dict) -> str:
        """Render against ``profile``."""
        return tr.render_template("stack/go/ci.yml.j2", profile)

    def test_renders_test_and_lint_jobs(self) -> None:
        """Go stack has both ``test`` and ``lint`` jobs."""
        rendered = self._render({"coverage": {}})
        doc = yaml.safe_load(rendered)
        self.assertEqual(doc["name"], "Go CI")
        self.assertIn("test", doc["jobs"])
        self.assertIn("lint", doc["jobs"])

    def test_default_go_version(self) -> None:
        """Default Go version is 1.23 when profile omits ``coverage.setup.go``."""
        rendered = self._render({"coverage": {}})
        self.assertIn('go-version: "1.23"', rendered)

    def test_custom_go_version_propagates(self) -> None:
        """``coverage.setup.go`` propagates into setup-go@v5."""
        rendered = self._render({"coverage": {"setup": {"go": "1.24"}}})
        self.assertIn('go-version: "1.24"', rendered)

    def test_default_coverage_command_has_race_and_atomic(self) -> None:
        """Default command enables ``-race`` + ``-covermode=atomic``."""
        rendered = self._render({"coverage": {}})
        self.assertIn("-race", rendered)
        self.assertIn("-covermode=atomic", rendered)
        self.assertIn("-coverprofile=coverage.out", rendered)

    def test_golangci_lint_action_pinned(self) -> None:
        """golangci-lint action is pinned by SHA per supply-chain contract."""
        rendered = self._render({"coverage": {}})
        self.assertIn(
            "golangci/golangci-lint-action@2226d7cb06a077cd73e56eedd38eecad18e5d837",
            rendered,
        )

    def test_list_templates_includes_go_ci(self) -> None:
        """``list_templates('go')`` surfaces the file."""
        mapping = tr.list_templates("go")
        self.assertIn("stack/go/ci.yml.j2", mapping)
        self.assertEqual(mapping["stack/go/ci.yml.j2"], "ci.yml")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
