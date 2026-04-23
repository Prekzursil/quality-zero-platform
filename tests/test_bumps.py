"""Tests for Phase 5 ``scripts.quality.bumps`` — bump recipe primitives."""

from __future__ import absolute_import

import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts.quality import bumps


def _write_recipe(dirpath: Path, body: str, name: str = "recipe.yml") -> Path:
    """Helper: write ``body`` to ``dirpath/name`` and return the path."""
    path = dirpath / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


class LoadBumpRecipeTests(unittest.TestCase):
    """``load_bump_recipe`` parses + validates the recipe YAML."""

    def test_valid_recipe_returns_normalised_dict(self) -> None:
        """Canonical Node 20→24 recipe round-trips through the loader."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_recipe(Path(tmp), """
                name: Node 20 -> 24
                target:
                  - file_glob: "**/ci.yml"
                    yaml_path: "jobs.*.steps[?.uses contains 'setup-node'].with.node-version"
                    value: '24'
                affects_stacks: [fullstack-web]
                staging_repos:
                  - Prekzursil/env-inspector
                full_rollout_after_staging: true
                rollback_on_failure: true
            """)
            recipe = bumps.load_bump_recipe(path)
        self.assertEqual(recipe["name"], "Node 20 -> 24")
        self.assertEqual(len(recipe["target"]), 1)
        self.assertEqual(recipe["target"][0]["value"], "24")
        self.assertIn("fullstack-web", recipe["affects_stacks"])
        self.assertTrue(recipe["full_rollout_after_staging"])

    def test_missing_required_field_raises(self) -> None:
        """Recipe missing ``name`` is rejected with clear message."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_recipe(Path(tmp), """
                target:
                  - file_glob: "**/ci.yml"
                    yaml_path: "x.y"
                    value: '24'
                affects_stacks: [x]
                staging_repos: [a/b]
            """)
            with self.assertRaises(bumps.BumpRecipeError) as ctx:
                bumps.load_bump_recipe(path)
            self.assertIn("name", str(ctx.exception))

    def test_target_must_be_non_empty_list(self) -> None:
        """Recipe with empty ``target`` is rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_recipe(Path(tmp), """
                name: empty
                target: []
                affects_stacks: [x]
                staging_repos: [a/b]
            """)
            with self.assertRaises(bumps.BumpRecipeError):
                bumps.load_bump_recipe(path)

    def test_target_entry_requires_file_glob_yaml_path_value(self) -> None:
        """Each target entry must have all three required keys."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_recipe(Path(tmp), """
                name: partial
                target:
                  - file_glob: "**/ci.yml"
                    yaml_path: "x.y"
                    # missing: value
                affects_stacks: [x]
                staging_repos: [a/b]
            """)
            with self.assertRaises(bumps.BumpRecipeError):
                bumps.load_bump_recipe(path)

    def test_staging_repos_must_be_non_empty(self) -> None:
        """Empty staging_repos blocks rollout — rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_recipe(Path(tmp), """
                name: no-staging
                target:
                  - file_glob: "**/ci.yml"
                    yaml_path: "x.y"
                    value: '24'
                affects_stacks: [x]
                staging_repos: []
            """)
            with self.assertRaises(bumps.BumpRecipeError):
                bumps.load_bump_recipe(path)

    def test_staging_repo_slug_must_have_owner(self) -> None:
        """Staging repos must be ``owner/name``, not bare ``name``."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_recipe(Path(tmp), """
                name: bad-slug
                target:
                  - file_glob: "**/ci.yml"
                    yaml_path: "x.y"
                    value: '24'
                affects_stacks: [x]
                staging_repos: [just-a-name]
            """)
            with self.assertRaises(bumps.BumpRecipeError):
                bumps.load_bump_recipe(path)

    def test_defaults_applied_when_optional_fields_missing(self) -> None:
        """``full_rollout_after_staging`` and ``rollback_on_failure`` default to True."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_recipe(Path(tmp), """
                name: minimal
                target:
                  - file_glob: "**/ci.yml"
                    yaml_path: "x.y"
                    value: '24'
                affects_stacks: [x]
                staging_repos: [a/b]
            """)
            recipe = bumps.load_bump_recipe(path)
        self.assertTrue(recipe["full_rollout_after_staging"])
        self.assertTrue(recipe["rollback_on_failure"])

    def test_yaml_root_must_be_mapping(self) -> None:
        """A top-level list or scalar is rejected with clear message."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recipe.yml"
            path.write_text("- not-a-mapping\n", encoding="utf-8")
            with self.assertRaises(bumps.BumpRecipeError) as ctx:
                bumps.load_bump_recipe(path)
            self.assertIn("mapping", str(ctx.exception))

    def test_target_entry_that_is_not_mapping_rejected(self) -> None:
        """``target: [scalar]`` rejected — each entry must be a mapping."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_recipe(Path(tmp), """
                name: bad-target
                target:
                  - just-a-string
                affects_stacks: [x]
                staging_repos: [a/b]
            """)
            with self.assertRaises(bumps.BumpRecipeError) as ctx:
                bumps.load_bump_recipe(path)
            self.assertIn("target[0]", str(ctx.exception))


class ResolveTargetFilesTests(unittest.TestCase):
    """``resolve_target_files`` expands file_globs against a repo root."""

    def test_glob_matches_nested_ci_yml(self) -> None:
        """Glob ``**/ci.yml`` finds files in top + nested dirs."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github" / "workflows").mkdir(parents=True)
            ci_a = root / ".github" / "workflows" / "ci.yml"
            ci_a.write_text("name: a\n", encoding="utf-8")
            (root / "sub" / ".github" / "workflows").mkdir(parents=True)
            ci_b = root / "sub" / ".github" / "workflows" / "ci.yml"
            ci_b.write_text("name: b\n", encoding="utf-8")
            (root / "other.yml").write_text("name: other\n", encoding="utf-8")

            targets = [{"file_glob": "**/ci.yml", "yaml_path": "x", "value": "y"}]
            files = bumps.resolve_target_files(root, targets)
        self.assertEqual(
            sorted(p.relative_to(root).as_posix() for p in files),
            [".github/workflows/ci.yml", "sub/.github/workflows/ci.yml"],
        )

    def test_no_matches_returns_empty_list(self) -> None:
        """Unmatched glob returns ``[]`` — caller decides whether to warn."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "unrelated.txt").write_text("x", encoding="utf-8")
            targets = [{"file_glob": "**/ci.yml", "yaml_path": "x", "value": "y"}]
            files = bumps.resolve_target_files(root, targets)
        self.assertEqual(files, [])

    def test_multiple_targets_deduped(self) -> None:
        """If 2 targets match the same file, it's only returned once."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ci.yml").write_text("name: a\n", encoding="utf-8")
            targets = [
                {"file_glob": "**/ci.yml", "yaml_path": "x", "value": "y"},
                {"file_glob": "ci.yml", "yaml_path": "x", "value": "y"},
            ]
            files = bumps.resolve_target_files(root, targets)
        self.assertEqual(len(files), 1)

    def test_directory_matches_are_skipped(self) -> None:
        """Glob matches that are directories (not files) are excluded."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ci.yml").mkdir()  # a directory NAMED ci.yml
            (root / "sub" / "ci.yml").parent.mkdir(parents=True)
            (root / "sub" / "ci.yml").write_text("x", encoding="utf-8")
            targets = [{"file_glob": "**/ci.yml", "yaml_path": "x", "value": "y"}]
            files = bumps.resolve_target_files(root, targets)
        # Only the real file under sub/ci.yml; the directory ci.yml is skipped.
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "ci.yml")
        self.assertEqual(files[0].parent.name, "sub")


class SampleNodeRecipeIsValidTests(unittest.TestCase):
    """The shipped sample recipe under ``profiles/bumps/`` loads cleanly."""

    def test_sample_recipe_loads_and_has_node_24(self) -> None:
        """The canonical Node 20→24 recipe survives the loader round-trip."""
        sample_path = (
            Path(__file__).resolve().parents[1]
            / "profiles" / "bumps" / "2026-04-23-node-24.yml"
        )
        recipe = bumps.load_bump_recipe(sample_path)
        self.assertIn("node", recipe["name"].lower())
        # Last target (or any) should have 24 in its value.
        values = [t["value"] for t in recipe["target"]]
        self.assertIn("24", values)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
