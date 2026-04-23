"""Pure-logic coverage for ``scripts/quality/fleet_inventory.py``.

Only the filter + diff layer is exercised here; subprocess and I/O
layers land in their own test modules as those commits arrive.
"""

from __future__ import absolute_import

import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any, List, Sequence, Tuple

from scripts.quality.fleet_inventory import (
    FleetDiff,
    PRIVATE_INCLUDE_SLUGS,
    build_expected_fleet,
    diff_fleet,
    fetch_authenticated_repos,
    fetch_user_repos,
    load_inventory_slugs,
    merge_repo_lists,
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


class FetchReposViaGhTests(unittest.TestCase):
    """The ``gh api`` subprocess wrapper — mocked runner, no network."""

    def _fake_runner(
        self, payload: Any, *, returncode: int = 0
    ) -> subprocess.CompletedProcess[str]:
        """Return a CompletedProcess the code under test can consume."""
        return subprocess.CompletedProcess(
            args=[],
            returncode=returncode,
            stdout=json.dumps(payload),
            stderr="",
        )

    def _capturing_runner(
        self, payload: Any
    ) -> Tuple[List[Sequence[str]], Any]:
        """Capture the ``args`` passed to subprocess.run for assertion."""
        captured: List[Sequence[str]] = []

        def runner(args: Sequence[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append(list(args))
            return self._fake_runner(payload)

        return captured, runner

    def test_fetch_user_repos_flattens_paginated_payload(self) -> None:
        # ``gh api --paginate --slurp`` returns [[page1], [page2], ...]
        payload = [
            [_repo(name="alpha"), _repo(name="beta")],
            [_repo(name="gamma")],
        ]
        _captured, runner = self._capturing_runner(payload)
        result = fetch_user_repos("Prekzursil", runner=runner)
        self.assertEqual(
            [r["full_name"] for r in result],
            ["Prekzursil/alpha", "Prekzursil/beta", "Prekzursil/gamma"],
        )

    def test_fetch_user_repos_accepts_legacy_flat_payload(self) -> None:
        # Older ``gh`` versions without --slurp return a flat array
        payload = [_repo(name="alpha"), _repo(name="beta")]
        _captured, runner = self._capturing_runner(payload)
        result = fetch_user_repos("Prekzursil", runner=runner)
        self.assertEqual(
            [r["full_name"] for r in result],
            ["Prekzursil/alpha", "Prekzursil/beta"],
        )

    def test_fetch_user_repos_targets_owner_endpoint(self) -> None:
        captured, runner = self._capturing_runner([])
        fetch_user_repos("Prekzursil", runner=runner)
        self.assertEqual(len(captured), 1)
        args = captured[0]
        # first element is the binary, rest are args
        self.assertEqual(args[0], "gh")
        self.assertIn("--paginate", args)
        self.assertIn("--slurp", args)
        self.assertTrue(
            any("/users/Prekzursil/repos" in str(a) for a in args),
            f"expected owner endpoint in args, got {args!r}",
        )

    def test_fetch_authenticated_repos_uses_user_endpoint(self) -> None:
        captured, runner = self._capturing_runner([])
        fetch_authenticated_repos(runner=runner)
        self.assertEqual(len(captured), 1)
        args = captured[0]
        self.assertTrue(
            any("/user/repos" in str(a) for a in args),
            f"expected /user/repos in args, got {args!r}",
        )

    def test_fetch_user_repos_propagates_gh_failure(self) -> None:
        def runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            raise subprocess.CalledProcessError(1, ["gh"], stderr="rate limit")

        with self.assertRaises(subprocess.CalledProcessError):
            fetch_user_repos("Prekzursil", runner=runner)


class MergeRepoListsTests(unittest.TestCase):
    """De-duplication on ``full_name`` when combining owner + auth lists."""

    def test_deduplicates_preserving_first_seen(self) -> None:
        public = [_repo(name="alpha"), _repo(name="beta")]
        private = [_repo(name="alpha", private=True), _repo(name="secret", private=True)]
        merged = merge_repo_lists(public, private)
        slugs = [r["full_name"] for r in merged]
        self.assertEqual(slugs, ["Prekzursil/alpha", "Prekzursil/beta", "Prekzursil/secret"])
        # First-seen wins → alpha remains public
        alpha = next(r for r in merged if r["full_name"] == "Prekzursil/alpha")
        self.assertFalse(alpha["private"])

    def test_empty_inputs(self) -> None:
        self.assertEqual(merge_repo_lists([], []), [])


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
