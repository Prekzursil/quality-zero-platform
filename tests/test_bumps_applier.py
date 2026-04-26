"""Tests for the bump applier added to ``scripts.quality.bumps``.

The applier is the missing piece that makes
``reusable-bumps.yml`` actually rewrite consumer-repo files. It uses
the OPTIONAL ``replace: {pattern, replacement}`` block on each
recipe target — a pragmatic regex-based approach that handles the
Node 20→24 canary without needing a full yamlpath evaluator.
"""

from __future__ import absolute_import

import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts.quality import bumps


class ApplyTextTests(unittest.TestCase):
    """``apply_bump_text`` rewrites a single string by regex."""

    def test_basic_substitution(self) -> None:
        """Plain pattern → replacement."""
        text = "node-version: '20'\nfoo: bar\n"
        new_text, count = bumps.apply_bump_text(
            text,
            pattern=r"node-version:\s*['\"]20['\"]",
            replacement="node-version: '24'",
        )
        self.assertEqual(new_text, "node-version: '24'\nfoo: bar\n")
        self.assertEqual(count, 1)

    def test_multiple_matches_all_replaced(self) -> None:
        """All matches replaced; count = total."""
        text = "a: '20'\nb: '20'\n"
        new_text, count = bumps.apply_bump_text(
            text,
            pattern=r"'20'",
            replacement="'24'",
        )
        self.assertEqual(count, 2)
        self.assertNotIn("'20'", new_text)

    def test_no_matches_returns_input(self) -> None:
        """No match → original text + count 0."""
        text = "node-version: '24'\n"
        new_text, count = bumps.apply_bump_text(
            text, pattern=r"'20'", replacement="'24'",
        )
        self.assertEqual(new_text, text)
        self.assertEqual(count, 0)

    def test_invalid_regex_raises(self) -> None:
        """Malformed regex surfaces as ``re.error``."""
        with self.assertRaises(Exception):
            bumps.apply_bump_text(
                "x", pattern=r"[unclosed", replacement="y",
            )


class ApplyFilesTests(unittest.TestCase):
    """``apply_bump_files`` walks file_globs + applies regex per target."""

    def test_writes_changed_files_only(self) -> None:
        """Only files that match get rewritten; others byte-identical."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github" / "workflows").mkdir(parents=True)
            ci_with_node = root / ".github" / "workflows" / "ci.yml"
            ci_with_node.write_text(textwrap.dedent("""\
                jobs:
                  build:
                    steps:
                      - uses: actions/setup-node@v4
                        with:
                          node-version: '20'
                """), encoding="utf-8")
            ci_without_node = root / ".github" / "workflows" / "lint.yml"
            ci_without_node.write_text("name: lint\n", encoding="utf-8")

            recipe_targets = [{
                "file_glob": "**/.github/workflows/*.yml",
                "yaml_path": "jobs.*.steps[?].with.node-version",
                "value": "24",
                "replace": {
                    "pattern": r"node-version:\s*['\"]20['\"]",
                    "replacement": "node-version: '24'",
                },
            }]
            results = bumps.apply_bump_files(
                repo_root=root, targets=recipe_targets,
            )
            self.assertEqual(results["bumped_files"], 1)
            self.assertEqual(results["bumped_total"], 1)
            # Changed file rewritten:
            self.assertIn("'24'", ci_with_node.read_text(encoding="utf-8"))
            # Unchanged file untouched (no need to re-write a no-op):
            self.assertEqual(
                ci_without_node.read_text(encoding="utf-8"), "name: lint\n",
            )

    def test_targets_without_replace_are_skipped(self) -> None:
        """Recipe targets that lack a ``replace`` block don't crash;
        they're skipped (with the abstract yaml_path future-work TODO)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ci.yml").write_text("node-version: '20'\n", encoding="utf-8")
            results = bumps.apply_bump_files(
                repo_root=root,
                targets=[{
                    "file_glob": "**/ci.yml",
                    "yaml_path": "x.y",
                    "value": "24",
                    # NO replace block
                }],
            )
        self.assertEqual(results["bumped_files"], 0)
        self.assertEqual(results["bumped_total"], 0)
        self.assertGreater(len(results["skipped_targets"]), 0)

    def test_multiple_targets_aggregate_counts(self) -> None:
        """Two targets matching the same file → counts add up correctly."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ci = root / "ci.yml"
            ci.write_text("a: 20\nb: 20\n", encoding="utf-8")
            results = bumps.apply_bump_files(
                repo_root=root,
                targets=[
                    {
                        "file_glob": "ci.yml",
                        "yaml_path": "a", "value": "24",
                        "replace": {"pattern": r"^a: 20$",
                                    "replacement": "a: 24"},
                    },
                    {
                        "file_glob": "ci.yml",
                        "yaml_path": "b", "value": "24",
                        "replace": {"pattern": r"^b: 20$",
                                    "replacement": "b: 24"},
                    },
                ],
            )
            self.assertEqual(results["bumped_total"], 2)
            # Same file edited twice — count it once for `bumped_files`.
            self.assertEqual(results["bumped_files"], 1)
            self.assertEqual(ci.read_text(encoding="utf-8"), "a: 24\nb: 24\n")

    def test_empty_targets_returns_zeros(self) -> None:
        """Empty targets list → nothing changed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = bumps.apply_bump_files(repo_root=root, targets=[])
        self.assertEqual(results["bumped_total"], 0)
        self.assertEqual(results["bumped_files"], 0)

    def test_empty_pattern_skipped_like_missing_replace(self) -> None:
        """``replace`` block with empty ``pattern`` → target skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ci.yml").write_text("x\n", encoding="utf-8")
            results = bumps.apply_bump_files(
                repo_root=root,
                targets=[{
                    "file_glob": "**/ci.yml",
                    "yaml_path": "x",
                    "value": "y",
                    "replace": {"pattern": "", "replacement": "y"},
                }],
            )
            self.assertEqual(results["bumped_total"], 0)
            self.assertIn(0, results["skipped_targets"])

    def test_directory_match_skipped(self) -> None:
        """Glob hit that's a directory (not a file) is silently skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A DIRECTORY named ci.yml — should not be treated as a file.
            (root / "ci.yml").mkdir()
            # And a real file under sub/ci.yml — should still be processed.
            (root / "sub").mkdir()
            real = root / "sub" / "ci.yml"
            real.write_text("node-version: '20'\n", encoding="utf-8")
            results = bumps.apply_bump_files(
                repo_root=root,
                targets=[{
                    "file_glob": "**/ci.yml",
                    "yaml_path": "x", "value": "y",
                    "replace": {
                        "pattern": r"'20'",
                        "replacement": "'24'",
                    },
                }],
            )
            self.assertEqual(results["bumped_files"], 1)
            self.assertIn("'24'", real.read_text(encoding="utf-8"))


class CanaryRecipeIntegrationTests(unittest.TestCase):
    """Shipped Node-canary recipe applies cleanly to a synthetic event-link CI."""

    def test_node_canary_replaces_setup_node_v20(self) -> None:
        """Loading the canary + applying it to a synthetic CI flips '20' → '24'."""
        recipe_path = (
            Path(__file__).resolve().parents[1]
            / "profiles" / "bumps" / "2026-04-23-node-24.yml"
        )
        recipe = bumps.load_bump_recipe(recipe_path)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github" / "workflows").mkdir(parents=True)
            (root / ".github" / "workflows" / "ci.yml").write_text(
                "      - uses: actions/setup-node@v4\n"
                "        with:\n"
                "          node-version: '20'\n",
                encoding="utf-8",
            )
            results = bumps.apply_bump_files(
                repo_root=root, targets=recipe["target"],
            )
        # Either the canary recipe ships a replace block (then bumped > 0)
        # OR it's still abstract (then skipped). The shipped recipe MUST
        # include a replace block for the bumps wave to actually do work.
        self.assertGreater(
            results["bumped_total"], 0,
            "the canonical Node 20→24 recipe must include a `replace` block "
            "on at least one target; otherwise `reusable-bumps.yml` can't "
            "actually rewrite consumer files",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
