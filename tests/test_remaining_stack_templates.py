"""Smoke-level render tests for the remaining Phase 3 stack templates.

Ships ``swift``, ``cpp-cmake``, ``dotnet-wpf``, ``gradle-java``, and
``fullstack-web`` stacks. Each test renders the template against a
minimal profile and asserts the output is parseable YAML with the
expected top-level job shape.
"""

from __future__ import absolute_import

import unittest

import yaml  # type: ignore[import-untyped]

from scripts.quality import template_render as tr


def _render(stack: str, profile: dict) -> str:
    """Render ``stack/<stack>/ci.yml.j2`` against ``profile``."""
    return tr.render_template(f"stack/{stack}/ci.yml.j2", profile)


class SwiftCiTemplateTests(unittest.TestCase):
    """``stack/swift/ci.yml.j2`` renders the macOS workflow."""

    def test_runs_on_macos_with_test_and_lint_jobs(self) -> None:
        """macOS runner is mandatory for xcodebuild."""
        doc = yaml.safe_load(_render("swift", {"coverage": {}}))
        self.assertEqual(doc["name"], "Swift CI")
        self.assertEqual(doc["jobs"]["test"]["runs-on"], "macos-latest")
        self.assertEqual(doc["jobs"]["lint"]["runs-on"], "macos-latest")

    def test_list_templates_surfaces_swift_ci(self) -> None:
        """``list_templates('swift')`` finds the file."""
        mapping = tr.list_templates("swift")
        self.assertEqual(mapping["stack/swift/ci.yml.j2"], "ci.yml")


class CppCmakeCiTemplateTests(unittest.TestCase):
    """``stack/cpp-cmake/ci.yml.j2`` configures a CMake build + ctest."""

    def test_has_test_and_lint_jobs(self) -> None:
        """CMake stack has ``test`` + ``lint`` jobs on ubuntu-latest."""
        doc = yaml.safe_load(_render("cpp-cmake", {"coverage": {}}))
        self.assertEqual(doc["name"], "C++ CMake CI")
        self.assertEqual(doc["jobs"]["test"]["runs-on"], "ubuntu-latest")
        self.assertIn("lint", doc["jobs"])

    def test_coverage_flags_enabled_in_cmake_configure(self) -> None:
        """CMake is configured with ``--coverage -O0 -g`` for lcov."""
        rendered = _render("cpp-cmake", {"coverage": {}})
        self.assertIn("--coverage -O0 -g", rendered)

    def test_list_templates_surfaces_cpp_cmake_ci(self) -> None:
        """``list_templates('cpp-cmake')`` finds the file."""
        mapping = tr.list_templates("cpp-cmake")
        self.assertEqual(mapping["stack/cpp-cmake/ci.yml.j2"], "ci.yml")


class DotnetWpfCiTemplateTests(unittest.TestCase):
    """``stack/dotnet-wpf/ci.yml.j2`` runs on windows-latest."""

    def test_runs_on_windows_latest(self) -> None:
        """WPF targets require the Windows Desktop SDK."""
        doc = yaml.safe_load(_render("dotnet-wpf", {"coverage": {}}))
        self.assertEqual(doc["name"], ".NET WPF CI")
        self.assertEqual(doc["jobs"]["test"]["runs-on"], "windows-latest")

    def test_default_dotnet_version_8_0_x(self) -> None:
        """``coverage.setup.dotnet`` defaults to 8.0.x."""
        rendered = _render("dotnet-wpf", {"coverage": {}})
        self.assertIn('dotnet-version: "8.0.x"', rendered)

    def test_custom_dotnet_version_propagates(self) -> None:
        """``coverage.setup.dotnet`` override reaches setup-dotnet."""
        rendered = _render(
            "dotnet-wpf", {"coverage": {"setup": {"dotnet": "9.0.x"}}}
        )
        self.assertIn('dotnet-version: "9.0.x"', rendered)


class GradleJavaCiTemplateTests(unittest.TestCase):
    """``stack/gradle-java/ci.yml.j2`` drives a Gradle build + Jacoco."""

    def test_default_java_version_21_temurin(self) -> None:
        """Defaults: JDK 21 + Temurin distribution."""
        rendered = _render("gradle-java", {"coverage": {}})
        self.assertIn('java-version: "21"', rendered)
        self.assertIn("distribution: temurin", rendered)

    def test_custom_java_distribution_propagates(self) -> None:
        """``coverage.setup.java.distribution`` is honoured."""
        rendered = _render(
            "gradle-java",
            {"coverage": {"setup": {"java": {"distribution": "zulu", "version": "17"}}}},
        )
        self.assertIn("distribution: zulu", rendered)
        self.assertIn('java-version: "17"', rendered)

    def test_default_command_includes_jacoco_and_static_gates(self) -> None:
        """Default gradle command runs build + jacoco + checkstyle + spotbugs."""
        rendered = _render("gradle-java", {"coverage": {}})
        self.assertIn("./gradlew build jacocoTestReport checkstyleMain spotbugsMain", rendered)


class FullstackWebCiTemplateTests(unittest.TestCase):
    """``stack/fullstack-web/ci.yml.j2`` composes backend + frontend + integration."""

    def test_three_parallel_jobs(self) -> None:
        """Backend + frontend + integration all appear."""
        doc = yaml.safe_load(_render(
            "fullstack-web",
            {"coverage": {"setup": {"python": "3.12", "node": "20"}}},
        ))
        self.assertEqual(doc["name"], "Fullstack Web CI")
        self.assertIn("backend", doc["jobs"])
        self.assertIn("frontend", doc["jobs"])
        self.assertIn("integration", doc["jobs"])

    def test_integration_waits_for_backend_and_frontend(self) -> None:
        """Integration depends on both coverage lanes via ``needs:``."""
        doc = yaml.safe_load(_render(
            "fullstack-web",
            {"coverage": {"setup": {"python": "3.12", "node": "20"}}},
        ))
        self.assertIn("backend", doc["jobs"]["integration"]["needs"])
        self.assertIn("frontend", doc["jobs"]["integration"]["needs"])

    def test_backend_uses_pytest_cov_app(self) -> None:
        """Backend job runs pytest with ``--cov=app`` + branch coverage."""
        rendered = _render(
            "fullstack-web",
            {"coverage": {"setup": {"python": "3.12", "node": "20"}}},
        )
        self.assertIn(
            "pytest --cov=app --cov-branch --cov-report=xml:coverage.xml",
            rendered,
        )

    def test_frontend_uses_vitest_run_coverage(self) -> None:
        """Frontend job runs ``npx vitest run --coverage``."""
        rendered = _render(
            "fullstack-web",
            {"coverage": {"setup": {"python": "3.12", "node": "20"}}},
        )
        self.assertIn("npx vitest run --coverage", rendered)


class ThisPrStacksCoveredTests(unittest.TestCase):
    """Every stack this PR introduces has a renderable template.

    ``go`` + ``rust`` land in parallel PRs #95 + #96 — this suite only
    asserts the stacks added here so it can land independently.
    """

    STACKS_IN_THIS_PR = (
        "swift", "cpp-cmake", "dotnet-wpf", "gradle-java", "fullstack-web",
    )

    def test_every_stack_has_ci_yml(self) -> None:
        """``list_templates(stack)`` returns at least ``ci.yml`` for each stack."""
        for stack in self.STACKS_IN_THIS_PR:
            with self.subTest(stack=stack):
                mapping = tr.list_templates(stack)
                self.assertIn(f"stack/{stack}/ci.yml.j2", mapping)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
