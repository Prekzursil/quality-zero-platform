"""Pure-logic coverage for ``scripts/quality/fleet_inventory.py``.

Only the filter + diff layer is exercised here; subprocess and I/O
layers land in their own test modules as those commits arrive.
"""

from __future__ import absolute_import

import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts.quality.fleet_inventory import (
    FleetDiff,
    PRIVATE_INCLUDE_SLUGS,
    build_expected_fleet,
    diff_fleet,
    load_inventory_slugs,
)


def _repo(
    *,
    name: str,
    owner: str = "Prekzursil",
    fork: bool = False,
    private: bool = False,
    is_template: bool = False,
) -> dict:
    """Shape-mimicking a ``gh api repos/...`` entry."""
    return {
        "name": name,
        "full_name": f"{owner}/{name}",
        "owner": {"login": owner},
        "fork": fork,
        "private": private,
        "is_template": is_template,
    }


class BuildExpectedFleetTests(unittest.TestCase):
    """Fleet filter: owner public non-fork + explicit private exceptions."""

    def test_public_non_fork_included(self) -> None:
        repos = [_repo(name="event-link")]
        self.assertEqual(build_expected_fleet(repos), ["Prekzursil/event-link"])

    def test_fork_excluded(self) -> None:
        repos = [_repo(name="forked-repo", fork=True)]
        self.assertEqual(build_expected_fleet(repos), [])

    def test_template_excluded(self) -> None:
        repos = [_repo(name="template-repo", is_template=True)]
        self.assertEqual(build_expected_fleet(repos), [])

    def test_private_excluded_by_default(self) -> None:
        repos = [_repo(name="secret-service", private=True)]
        self.assertEqual(build_expected_fleet(repos), [])

    def test_private_pbinfo_included_as_explicit_exception(self) -> None:
        repos = [_repo(name="pbinfo-get-unsolved", private=True)]
        self.assertEqual(
            build_expected_fleet(repos),
            ["Prekzursil/pbinfo-get-unsolved"],
        )

    def test_dedupes_and_sorts_output(self) -> None:
        repos = [
            _repo(name="zeta"),
            _repo(name="alpha"),
            _repo(name="alpha"),  # dup
        ]
        self.assertEqual(
            build_expected_fleet(repos),
            ["Prekzursil/alpha", "Prekzursil/zeta"],
        )

    def test_private_include_frozen_set(self) -> None:
        """The exception list must be immutable to prevent silent edits."""
        self.assertIsInstance(PRIVATE_INCLUDE_SLUGS, frozenset)
        self.assertIn("Prekzursil/pbinfo-get-unsolved", PRIVATE_INCLUDE_SLUGS)


class LoadInventorySlugsTests(unittest.TestCase):
    """inventory/repos.yml reader."""

    def _write(self, body: str, tmpdir: Path) -> Path:
        inventory = tmpdir / "repos.yml"
        inventory.write_text(textwrap.dedent(body), encoding="utf-8")
        return inventory

    def test_reads_and_sorts_slugs(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            inventory = self._write(
                """
                version: 1
                repos:
                  - slug: Prekzursil/zeta
                  - slug: Prekzursil/alpha
                """,
                Path(raw_tmp),
            )
            self.assertEqual(
                load_inventory_slugs(inventory),
                ["Prekzursil/alpha", "Prekzursil/zeta"],
            )

    def test_missing_repos_key_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            inventory = self._write("version: 1\n", Path(raw_tmp))
            self.assertEqual(load_inventory_slugs(inventory), [])

    def test_ignores_non_mapping_entries(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            inventory = self._write(
                """
                repos:
                  - slug: Prekzursil/valid
                  - "a stray string, not a dict"
                  - slug: ""
                """,
                Path(raw_tmp),
            )
            self.assertEqual(
                load_inventory_slugs(inventory),
                ["Prekzursil/valid"],
            )


class DiffFleetTests(unittest.TestCase):
    """Missing vs. dead slug diff."""

    def test_missing_and_dead_surfaced(self) -> None:
        diff = diff_fleet(
            expected=["Prekzursil/a", "Prekzursil/b"],
            inventoried=["Prekzursil/b", "Prekzursil/c"],
        )
        self.assertEqual(diff.missing, ["Prekzursil/a"])
        self.assertEqual(diff.dead, ["Prekzursil/c"])

    def test_no_gap(self) -> None:
        diff = diff_fleet(
            expected=["Prekzursil/a"],
            inventoried=["Prekzursil/a"],
        )
        self.assertEqual(diff.missing, [])
        self.assertEqual(diff.dead, [])

    def test_returns_frozen_dataclass(self) -> None:
        diff = diff_fleet([], [])
        self.assertIsInstance(diff, FleetDiff)
        with self.assertRaises(Exception):
            diff.missing = ["mutate-attempt"]  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
