"""Render contract tests for ``scripts.quality.template_render``.

Phase 3 of ``docs/QZP-V2-DESIGN.md`` §4 ships per-stack Jinja2
templates that the drift-sync workflow materialises into consumer
repos. These tests pin the render contract:

* StrictUndefined enforcement — typos in a template raise instead of
  silently emitting empty strings (important because a missing flag
  could drop a safety gate).
* common/codecov.yml.j2 renders a parseable Codecov config with one
  ``flags:`` entry per ``coverage.inputs[]`` row.
* ``list_templates`` discovers the right template files for a given
  stack and maps each to its in-repo output path.
"""

from __future__ import absolute_import

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict

import yaml  # type: ignore[import-untyped]
from jinja2 import UndefinedError
from jinja2.exceptions import TemplateNotFound

from scripts.quality import template_render as tr


class CodecovTemplateRenderTests(unittest.TestCase):
    """``common/codecov.yml.j2`` renders a valid Codecov config."""

    @staticmethod
    def _render(profile: Dict) -> str:
        """Render the real shipped template against ``profile`` as context."""
        return tr.render_template("common/codecov.yml.j2", profile)

    def test_single_flag_profile_renders_valid_yaml(self) -> None:
        """Single-flag profile produces parseable YAML with one ``flags:`` entry."""
        profile = {
            "coverage": {
                "inputs": [
                    {
                        "name": "platform",
                        "flag": "platform",
                        "path": "coverage/platform-coverage.xml",
                        "format": "xml",
                    }
                ],
                "min_percent": 100.0,
            }
        }
        rendered = self._render(profile)
        parsed = yaml.safe_load(rendered)
        self.assertEqual(parsed["codecov"]["require_ci_to_pass"], True)
        self.assertIn("platform", parsed["flags"])
        # First segment of ``coverage/platform-coverage.xml`` is the source
        # root fallback when no explicit ``sources`` is declared.
        self.assertEqual(parsed["flags"]["platform"]["paths"], ["coverage/"])
        # Codecov accepts either ``100%`` or ``100.0%``; the template
        # renders the numeric-preserving form via ``round(2)``.
        self.assertEqual(
            parsed["coverage"]["status"]["project"]["default"]["target"],
            "100.0%",
        )

    def test_multi_flag_profile_renders_one_entry_per_input(self) -> None:
        """event-link-style profile with backend + frontend inputs."""
        profile = {
            "coverage": {
                "inputs": [
                    {"name": "backend", "flag": "backend", "path": "backend/coverage.xml"},
                    {
                        "name": "frontend",
                        "flag": "ui",
                        "path": "ui/coverage/lcov.info",
                    },
                ]
            }
        }
        parsed = yaml.safe_load(self._render(profile))
        self.assertEqual(set(parsed["flags"].keys()), {"backend", "ui"})
        self.assertEqual(parsed["flags"]["backend"]["paths"], ["backend/"])
        self.assertEqual(parsed["flags"]["ui"]["paths"], ["ui/"])

    def test_explicit_sources_override_path_heuristic(self) -> None:
        """When a profile declares ``sources``, the template uses them verbatim."""
        profile = {
            "coverage": {
                "inputs": [
                    {
                        "name": "fused",
                        "flag": "fused",
                        "path": "artifacts/merged.xml",
                        "sources": ["src/core/", "src/adapters/"],
                    }
                ]
            }
        }
        parsed = yaml.safe_load(self._render(profile))
        self.assertEqual(
            parsed["flags"]["fused"]["paths"],
            ["src/core/", "src/adapters/"],
        )

    def test_ignore_patterns_emit_ignore_block(self) -> None:
        """``coverage.ignore`` renders as a Codecov ``ignore:`` list."""
        profile = {
            "coverage": {
                "inputs": [
                    {"name": "x", "flag": "x", "path": "x.xml"},
                ],
                "ignore": ["docs/**", "generated/**"],
            }
        }
        parsed = yaml.safe_load(self._render(profile))
        self.assertEqual(parsed["ignore"], ["docs/**", "generated/**"])

    def test_omitted_ignore_does_not_emit_block(self) -> None:
        """Profiles without ``coverage.ignore`` render without an ``ignore:`` key."""
        profile = {
            "coverage": {
                "inputs": [{"name": "x", "flag": "x", "path": "x.xml"}],
            }
        }
        rendered = self._render(profile)
        self.assertNotIn("\nignore:", rendered)


class StrictUndefinedTests(unittest.TestCase):
    """Missing context values raise ``UndefinedError`` — no silent empties."""

    def test_undefined_variable_raises(self) -> None:
        """A template that references a missing key blows up loudly.

        This is what protects against a future template migration where
        a profile field is renamed and the renderer would otherwise
        silently emit an empty string into a safety-critical config.
        """
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "bad").mkdir()
            (tmp_root / "bad" / "x.yml.j2").write_text(
                "value: {{ missing_key }}\n", encoding="utf-8"
            )
            with self.assertRaises(UndefinedError):
                tr.render_template(
                    "bad/x.yml.j2",
                    {},
                    templates_root=tmp_root,
                )


class TemplateExistsTests(unittest.TestCase):
    """``template_exists`` distinguishes real vs missing templates."""

    def test_existing_template_returns_true(self) -> None:
        """The shipped ``common/codecov.yml.j2`` exists."""
        self.assertTrue(tr.template_exists("common/codecov.yml.j2"))

    def test_missing_template_returns_false(self) -> None:
        """A made-up path returns False rather than raising."""
        self.assertFalse(tr.template_exists("common/does-not-exist.j2"))

    def test_missing_template_raises_via_render(self) -> None:
        """``render_template`` still surfaces ``TemplateNotFound``."""
        with self.assertRaises(TemplateNotFound):
            tr.render_template("common/does-not-exist.j2", {})


class ListTemplatesTests(unittest.TestCase):
    """``list_templates`` walks common/ + the specific stack dir."""

    def test_unknown_stack_returns_common_templates_only(self) -> None:
        """A stack with no stack-specific dir still finds common/*.j2 files."""
        mapping = tr.list_templates("nonexistent-stack-xyz")
        # common/codecov.yml.j2 ships in this PR — other common templates
        # may land in future increments.
        self.assertIn("common/codecov.yml.j2", mapping)
        self.assertEqual(mapping["common/codecov.yml.j2"], "codecov.yml")

    def test_output_paths_strip_the_j2_suffix(self) -> None:
        """Output paths are the consumer-repo destinations, not template paths."""
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            common = tmp_root / "common"
            common.mkdir()
            (common / "dependabot.yml.j2").write_text("", encoding="utf-8")
            stack = tmp_root / "stack" / "go"
            stack.mkdir(parents=True)
            (stack / "ci.yml.j2").write_text("", encoding="utf-8")
            mapping = tr.list_templates("go", templates_root=tmp_root)
        self.assertEqual(mapping["common/dependabot.yml.j2"], "dependabot.yml")
        self.assertEqual(mapping["stack/go/ci.yml.j2"], "ci.yml")

    def test_non_j2_files_are_ignored(self) -> None:
        """``STACK.md`` and ``README.md`` don't appear in the mapping."""
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            stack = tmp_root / "stack" / "swift"
            stack.mkdir(parents=True)
            (stack / "STACK.md").write_text("# swift\n", encoding="utf-8")
            (stack / "config.toml.j2").write_text("", encoding="utf-8")
            mapping = tr.list_templates("swift", templates_root=tmp_root)
        self.assertEqual(list(mapping.keys()), ["stack/swift/config.toml.j2"])


class CoverageThresholdsTemplateTests(unittest.TestCase):
    """``common/coverage-thresholds.json.j2`` renders valid JSON."""

    @staticmethod
    def _render(profile: Dict) -> str:
        """Render the thresholds template."""
        return tr.render_template("common/coverage-thresholds.json.j2", profile)

    def test_all_thresholds_default_to_min_percent(self) -> None:
        """When only ``min_percent`` is set, every threshold mirrors it."""
        import json as _json

        rendered = self._render({"coverage": {"min_percent": 100.0}})
        data = _json.loads(rendered)
        self.assertEqual(data["lines"], 100.0)
        self.assertEqual(data["branches"], 100.0)
        self.assertEqual(data["functions"], 100.0)
        self.assertEqual(data["statements"], 100.0)

    def test_empty_coverage_falls_back_to_100(self) -> None:
        """A profile with no coverage thresholds still emits a 100% gate."""
        import json as _json

        rendered = self._render({"coverage": {}})
        data = _json.loads(rendered)
        self.assertEqual(data["lines"], 100)
        self.assertEqual(data["branches"], 100)


class DependabotTemplateTests(unittest.TestCase):
    """``common/dependabot.yml.j2`` renders valid Dependabot config."""

    @staticmethod
    def _render(profile: Dict) -> str:
        """Render the dependabot template."""
        return tr.render_template("common/dependabot.yml.j2", profile)

    def test_minimum_profile_renders_one_update(self) -> None:
        """One-ecosystem profile produces a parseable Dependabot config."""
        profile = {
            "dependabot": {
                "updates": [
                    {"ecosystem": "pip", "directory": "/backend"},
                ]
            }
        }
        data = yaml.safe_load(self._render(profile))
        self.assertEqual(data["version"], 2)
        self.assertEqual(len(data["updates"]), 1)
        update = data["updates"][0]
        self.assertEqual(update["package-ecosystem"], "pip")
        self.assertEqual(update["directory"], "/backend")
        self.assertEqual(update["schedule"]["interval"], "weekly")
        self.assertEqual(update["open-pull-requests-limit"], 10)

    def test_major_updates_are_ignored_by_default(self) -> None:
        """Every rendered update blocks major bumps — goes via ``bumps.yml``."""
        profile = {
            "dependabot": {
                "updates": [{"ecosystem": "npm", "directory": "/ui"}]
            }
        }
        data = yaml.safe_load(self._render(profile))
        ignores = data["updates"][0]["ignore"]
        self.assertEqual(ignores[0]["dependency-name"], "*")
        self.assertIn("version-update:semver-major", ignores[0]["update-types"])

    def test_no_dependabot_block_yields_empty_updates_list(self) -> None:
        """Profiles without ``dependabot`` still render a valid config."""
        rendered = self._render({})
        data = yaml.safe_load(rendered)
        self.assertEqual(data["version"], 2)
        # Empty update list is valid YAML; either ``updates: []`` or key absent.
        self.assertIn(data.get("updates", []), ([], None))


class CiFragmentTemplateTests(unittest.TestCase):
    """``common/ci-fragments/*.j2`` render reusable workflow step snippets."""

    def test_setup_python_defaults_to_3_12(self) -> None:
        """Fragment defaults to Python 3.12 when profile omits it."""
        rendered = tr.render_template(
            "common/ci-fragments/setup-python.yml.j2", {"coverage": {}}
        )
        self.assertIn("uses: actions/setup-python@v5", rendered)
        self.assertIn('python-version: "3.12"', rendered)

    def test_setup_python_honours_profile_version(self) -> None:
        """``coverage.setup.python`` propagates into the fragment."""
        rendered = tr.render_template(
            "common/ci-fragments/setup-python.yml.j2",
            {"coverage": {"setup": {"python": "3.13"}}},
        )
        self.assertIn('python-version: "3.13"', rendered)

    def test_setup_node_omitted_when_profile_has_no_node(self) -> None:
        """Python-only stacks render the setup-node fragment as empty."""
        rendered = tr.render_template(
            "common/ci-fragments/setup-node.yml.j2", {"coverage": {}}
        )
        self.assertNotIn("setup-node", rendered)

    def test_setup_node_renders_when_profile_declares_it(self) -> None:
        """Frontend stacks render the standard ``setup-node@v4`` step."""
        rendered = tr.render_template(
            "common/ci-fragments/setup-node.yml.j2",
            {"coverage": {"setup": {"node": "20"}}},
        )
        self.assertIn("uses: actions/setup-node@v4", rendered)
        self.assertIn('node-version: "20"', rendered)


class EnvironmentContractTests(unittest.TestCase):
    """The Jinja2 environment pins the platform's non-HTML render policy."""

    def test_trim_and_lstrip_blocks_enabled(self) -> None:
        """Block statements don't leave blank lines or leading whitespace."""
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "common").mkdir()
            template_text = textwrap.dedent(
                """\
                {% for name in names %}
                  - {{ name }}
                {% endfor %}
                """
            )
            (tmp_root / "common" / "list.yml.j2").write_text(
                template_text, encoding="utf-8"
            )
            rendered = tr.render_template(
                "common/list.yml.j2",
                {"names": ["alpha", "beta"]},
                templates_root=tmp_root,
            )
        self.assertIn("- alpha", rendered)
        self.assertIn("- beta", rendered)
        # ``trim_blocks`` removes the newline after ``{% for %}``; without
        # it the output would start with a blank line.
        self.assertFalse(rendered.startswith("\n"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
