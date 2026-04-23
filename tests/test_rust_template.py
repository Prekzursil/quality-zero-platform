"""Render tests for ``profiles/templates/stack/rust/ci.yml.j2``."""

from __future__ import absolute_import

import unittest

import yaml  # type: ignore[import-untyped]

from scripts.quality import template_render as tr


class RustCiTemplateTests(unittest.TestCase):
    """``stack/rust/ci.yml.j2`` renders a Rust CI workflow."""

    @staticmethod
    def _render(profile: dict) -> str:
        """Render against ``profile``."""
        return tr.render_template("stack/rust/ci.yml.j2", profile)

    def test_renders_test_and_lint_jobs(self) -> None:
        """Rust stack has both ``test`` and ``lint`` jobs."""
        rendered = self._render({"coverage": {}})
        doc = yaml.safe_load(rendered)
        self.assertEqual(doc["name"], "Rust CI")
        self.assertIn("test", doc["jobs"])
        self.assertIn("lint", doc["jobs"])

    def test_default_channel_is_stable(self) -> None:
        """``rust_channel`` defaults to ``stable``."""
        rendered = self._render({"coverage": {}})
        self.assertIn("toolchain: stable", rendered)

    def test_custom_channel_propagates(self) -> None:
        """``coverage.setup.rust_channel`` overrides default."""
        rendered = self._render({
            "coverage": {"setup": {"rust_channel": "nightly"}}
        })
        self.assertIn("toolchain: nightly", rendered)

    def test_llvm_cov_coverage_command(self) -> None:
        """Default coverage command uses cargo-llvm-cov with lcov output."""
        rendered = self._render({"coverage": {}})
        self.assertIn("cargo llvm-cov", rendered)
        self.assertIn("--lcov", rendered)
        self.assertIn("lcov.info", rendered)

    def test_clippy_all_warnings_are_errors(self) -> None:
        """Clippy is run with ``-D warnings`` to treat all warnings as errors."""
        rendered = self._render({"coverage": {}})
        self.assertIn("cargo clippy --all-features --all-targets -- -D warnings", rendered)

    def test_rustfmt_check_mode(self) -> None:
        """rustfmt runs in ``--check`` mode so it fails on un-formatted code."""
        rendered = self._render({"coverage": {}})
        self.assertIn("cargo fmt --all -- --check", rendered)

    def test_rust_toolchain_action_pinned(self) -> None:
        """``dtolnay/rust-toolchain`` is pinned by SHA per §A.2.4."""
        rendered = self._render({"coverage": {}})
        self.assertIn(
            "dtolnay/rust-toolchain@631a55b12751854ce901bb631d5902ceb48146f7",
            rendered,
        )

    def test_list_templates_includes_rust_ci(self) -> None:
        """``list_templates('rust')`` surfaces the file."""
        mapping = tr.list_templates("rust")
        self.assertIn("stack/rust/ci.yml.j2", mapping)
        self.assertEqual(mapping["stack/rust/ci.yml.j2"], "ci.yml")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
