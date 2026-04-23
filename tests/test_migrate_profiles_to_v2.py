"""Coverage for :mod:`scripts.quality.migrate_profiles_to_v2`."""

from __future__ import absolute_import

import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any, Dict

import yaml  # type: ignore[import-untyped]

from scripts.quality.migrate_profiles_to_v2 import (
    main as migrate_main,
    migrate_profile,
    migrate_profile_file,
)


def _v1_profile_fixture() -> Dict[str, Any]:
    """Mimic a typical on-disk v1 profile (event-link-like shape)."""
    return {
        "slug": "Prekzursil/foo",
        "stack": "fullstack-web",
        "verify_command": "bash scripts/verify",
        "issue_policy": {
            "mode": "ratchet",
            "pr_behavior": "introduced_only",
            "main_behavior": "absolute",
        },
        "enabled_scanners": {
            "deepsource_visible": True,
            "codecov": False,
        },
        "coverage": {
            "command": "bash scripts/verify\n",
            "min_percent": 100.0,
            "inputs": [
                {
                    "name": "backend",
                    "path": "backend/coverage.xml",
                    "format": "xml",
                },
                {
                    "name": "frontend",
                    "path": "ui/coverage/cobertura-coverage.xml",
                    "format": "xml",
                },
            ],
        },
    }


class MigrateProfileTests(unittest.TestCase):
    """Pure migration semantics — no file I/O."""

    def test_adds_version_2(self) -> None:
        result = migrate_profile(_v1_profile_fixture())
        self.assertEqual(result["version"], 2)

    def test_version_keys_near_top_for_reviewable_diff(self) -> None:
        result = migrate_profile(_v1_profile_fixture())
        keys = list(result.keys())
        # Keys we want the reviewer to see first.
        self.assertEqual(keys[0], "slug")
        self.assertEqual(keys[1], "version")
        self.assertIn("mode", keys[:6])
        self.assertIn("scanners", keys[:6])
        self.assertIn("overrides", keys[:6])

    def test_mode_derived_from_legacy_issue_policy(self) -> None:
        result = migrate_profile(_v1_profile_fixture())
        self.assertIn("mode", result)
        self.assertEqual(result["mode"]["phase"], "ratchet")
        # Ratchet phase keeps the ratchet sub-block.
        self.assertIn("ratchet", result["mode"])

    def test_non_ratchet_mode_stays_compact(self) -> None:
        raw = _v1_profile_fixture()
        raw["issue_policy"]["mode"] = "zero"
        result = migrate_profile(raw)
        self.assertEqual(result["mode"], {"phase": "absolute"})

    def test_scanners_mapped_from_legacy_enabled(self) -> None:
        result = migrate_profile(_v1_profile_fixture())
        self.assertEqual(
            result["scanners"],
            {"deepsource_visible": {"severity": "block"}},
        )

    def test_coverage_inputs_get_flag_from_name(self) -> None:
        result = migrate_profile(_v1_profile_fixture())
        inputs = result["coverage"]["inputs"]
        self.assertEqual(inputs[0]["flag"], "backend")
        self.assertEqual(inputs[1]["flag"], "frontend")

    def test_existing_flag_is_preserved(self) -> None:
        raw = _v1_profile_fixture()
        raw["coverage"]["inputs"][0]["flag"] = "backend-unit"
        result = migrate_profile(raw)
        self.assertEqual(result["coverage"]["inputs"][0]["flag"], "backend-unit")

    def test_overrides_empty_list_added(self) -> None:
        result = migrate_profile(_v1_profile_fixture())
        self.assertEqual(result["overrides"], [])

    def test_migration_is_idempotent(self) -> None:
        once = migrate_profile(_v1_profile_fixture())
        twice = migrate_profile(once)
        self.assertEqual(once, twice)

    def test_legacy_fields_preserved(self) -> None:
        """issue_policy + enabled_scanners stay during the migration window."""
        result = migrate_profile(_v1_profile_fixture())
        self.assertIn("issue_policy", result)
        self.assertIn("enabled_scanners", result)


class MigrateProfileFileTests(unittest.TestCase):
    """File-level migration + idempotency."""

    def test_writes_changes_when_profile_is_v1(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            profile_path = Path(raw) / "foo.yml"
            profile_path.write_text(
                textwrap.dedent(
                    """
                    slug: Prekzursil/foo
                    stack: fullstack-web
                    issue_policy:
                      mode: ratchet
                    enabled_scanners:
                      deepsource_visible: true
                    coverage:
                      inputs:
                        - name: backend
                          path: backend/coverage.xml
                          format: xml
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            changed = migrate_profile_file(profile_path)
            self.assertTrue(changed)

            roundtrip = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
            self.assertEqual(roundtrip["version"], 2)
            self.assertEqual(
                roundtrip["coverage"]["inputs"][0]["flag"], "backend"
            )

    def test_no_change_when_already_v2(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            profile_path = Path(raw) / "foo.yml"
            first = migrate_profile(_v1_profile_fixture())
            profile_path.write_text(
                yaml.safe_dump(first, sort_keys=False),
                encoding="utf-8",
            )
            changed = migrate_profile_file(profile_path)
            self.assertFalse(changed)


class MigrationEdgeTests(unittest.TestCase):
    """Branches the main happy-path tests don't exercise on their own."""

    def test_declared_v1_version_bumps_to_v2(self) -> None:
        """A profile explicitly declaring version:1 is bumped to 2."""
        result = migrate_profile({"slug": "X", "version": 1})
        self.assertEqual(result["version"], 2)

    def test_garbage_version_falls_back_to_v2(self) -> None:
        """Non-integer version strings are replaced with v2."""
        result = migrate_profile({"slug": "X", "version": "not-a-number"})
        self.assertEqual(result["version"], 2)

    def test_non_mapping_coverage_input_is_preserved(self) -> None:
        """Stray non-mapping inputs pass through untouched (no flag injection)."""
        result = migrate_profile(
            {
                "slug": "X",
                "coverage": {"inputs": ["stray-string", {"name": "backend"}]},
            }
        )
        inputs = result["coverage"]["inputs"]
        self.assertEqual(inputs[0], "stray-string")
        self.assertEqual(inputs[1]["flag"], "backend")

    def test_migrate_profile_file_rejects_non_dict_payload(self) -> None:
        """A YAML file that parses to a list (not a dict) is skipped."""
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "weird.yml"
            path.write_text("- not a profile\n- just a list\n", encoding="utf-8")
            from scripts.quality.migrate_profiles_to_v2 import migrate_profile_file

            self.assertFalse(migrate_profile_file(path))


class MigrateCLITests(unittest.TestCase):
    """The CLI entrypoint glues it all together."""

    def test_dry_run_does_not_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            profile_dir = Path(raw)
            profile_path = profile_dir / "foo.yml"
            profile_path.write_text(
                "slug: Prekzursil/foo\nstack: fullstack-web\n",
                encoding="utf-8",
            )
            original = profile_path.read_text(encoding="utf-8")
            exit_code = migrate_main(
                ["--profiles-dir", str(profile_dir), "--dry-run"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                profile_path.read_text(encoding="utf-8"), original
            )

    def test_real_run_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            profile_dir = Path(raw)
            profile_path = profile_dir / "foo.yml"
            profile_path.write_text(
                "slug: Prekzursil/foo\nstack: fullstack-web\n",
                encoding="utf-8",
            )
            exit_code = migrate_main(
                ["--profiles-dir", str(profile_dir)]
            )
            self.assertEqual(exit_code, 0)
            migrated = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["version"], 2)

    def test_missing_dir_exit_two(self) -> None:
        """A missing profiles dir exits 2 for the CLI."""
        exit_code = migrate_main(
            ["--profiles-dir", "/definitely/not/a/real/path"]
        )
        self.assertEqual(exit_code, 2)

    def test_main_with_no_migrations_needed_prints_none_needed(self) -> None:
        """When every profile is already v2, main() reports no migrations."""
        with tempfile.TemporaryDirectory() as raw:
            profile_dir = Path(raw)
            # Write a profile that's already fully v2 so the migrator won't touch it.
            v2 = migrate_profile(_v1_profile_fixture())
            (profile_dir / "ready.yml").write_text(
                yaml.safe_dump(v2, sort_keys=False), encoding="utf-8"
            )
            # Plus a non-mapping YAML file that the loop must skip cleanly.
            (profile_dir / "stray.yml").write_text(
                "- just a list\n- not a dict\n", encoding="utf-8"
            )
            from contextlib import redirect_stdout
            import io

            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = migrate_main(["--profiles-dir", str(profile_dir)])
            self.assertEqual(exit_code, 0)
            self.assertIn("No profiles required migration.", buf.getvalue())

    def test_migrate_profile_with_non_mapping_coverage_field(self) -> None:
        """When ``coverage`` is not a mapping, it's left alone."""
        result = migrate_profile({"slug": "X", "coverage": "not-a-dict"})
        self.assertEqual(result["coverage"], "not-a-dict")

    def test_migrate_profile_with_non_list_inputs_is_left_alone(self) -> None:
        """When ``coverage.inputs`` isn't a list, don't touch it."""
        result = migrate_profile(
            {"slug": "X", "coverage": {"inputs": "not-a-list"}}
        )
        self.assertEqual(result["coverage"]["inputs"], "not-a-list")


if __name__ == "__main__":
    unittest.main()
