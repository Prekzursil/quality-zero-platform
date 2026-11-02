"""Edge-case tests for small helpers the larger suites never reach.

* ``check_sonar_zero._is_ratchet_scoped`` - when ratchet mode may keep the baseline;
* ``profile_normalization`` - malformed Dependabot updates, unknown legacy
  issue-policy modes, non-string severities, malformed overrides;
* ``run_qlty_zero`` - the ``.qlty/qlty.toml`` ``[[smells.exclude]]`` filter.
"""

from __future__ import absolute_import

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from scripts.quality import check_sonar_zero as sonar
from scripts.quality import profile_normalization as normalization
from scripts.quality import run_qlty_zero as qlty


class SonarRatchetScopeTests(unittest.TestCase):
    """Ratchet mode keeps the baseline on PRs and feature branches, never on main."""

    def _scoped(self, **values: str) -> bool:
        return sonar._is_ratchet_scoped(Namespace(**values))

    def test_a_pull_request_is_always_scoped(self) -> None:
        """A PR run only fails on new issues."""
        self.assertTrue(self._scoped(pull_request="42", branch="main", main_branch="main"))

    def test_branch_runs_compare_against_the_main_branch(self) -> None:
        """A feature branch is scoped; main (default or named) and an unknown branch are absolute."""
        self.assertTrue(self._scoped(pull_request="", branch="feature/x", main_branch="main"))
        self.assertFalse(self._scoped(pull_request="", branch="main", main_branch=""))
        self.assertFalse(self._scoped(pull_request=" ", branch="trunk", main_branch="trunk"))
        self.assertFalse(self._scoped(pull_request="", branch="", main_branch="main"))


class ProfileNormalizationEdgeTests(unittest.TestCase):
    """Malformed profile fragments normalise to safe values instead of crashing."""

    def test_malformed_dependabot_updates_are_skipped(self) -> None:
        """Non-mapping entries and entries without an ecosystem or directory are dropped."""
        normalized = normalization.normalize_dependabot(
            {
                "updates": [
                    "pip",
                    {"ecosystem": "pip"},
                    {"directory": "/"},
                    {"ecosystem": " npm ", "directory": " /web "},
                ]
            }
        )
        self.assertEqual(normalized["updates"], [{"ecosystem": "npm", "directory": "/web"}])

    def test_unknown_legacy_issue_policy_modes_are_absolute(self) -> None:
        """Only an explicit `ratchet` keeps ratchet semantics."""
        self.assertEqual(normalization._phase_from_issue_policy({"mode": "audit"}), "absolute")
        self.assertEqual(normalization._phase_from_issue_policy({"mode": "ratchet"}), "ratchet")
        self.assertEqual(normalization._phase_from_issue_policy(None), "absolute")

    def test_severities_default_to_block(self) -> None:
        """A known severity is kept (case-folded); anything else - including non-strings - blocks."""
        self.assertEqual(normalization._coerce_severity(" WARN "), "warn")
        self.assertEqual(normalization._coerce_severity("loud"), "block")
        self.assertEqual(normalization._coerce_severity(None), "block")
        self.assertEqual(normalization._coerce_severity(3), "block")

    def test_malformed_overrides_are_skipped(self) -> None:
        """A non-mapping override entry is ignored, a non-list block is no overrides."""
        self.assertEqual(normalization.normalize_overrides(["not-a-mapping"]), [])
        self.assertEqual(normalization.normalize_overrides("nope"), [])


class QltySmellsExcludeTests(unittest.TestCase):
    """`[[smells.exclude]]` path patterns in `.qlty/qlty.toml` filter smells findings."""

    def setUp(self) -> None:
        """A scratch repo root."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)

    def test_no_qlty_toml_means_no_patterns(self) -> None:
        """A repo without the file excludes nothing."""
        self.assertEqual(tuple(qlty._smells_exclude_lines(self.repo / ".qlty" / "qlty.toml")), ())
        self.assertEqual(qlty._load_smells_exclude_patterns(self.repo), [])

    def test_patterns_are_read_from_each_exclude_block_only(self) -> None:
        """A `path` counts only inside a [[smells.exclude]] block; unquoted values and other tables are ignored."""
        (self.repo / ".qlty").mkdir()
        (self.repo / ".qlty" / "qlty.toml").write_text(
            "\n".join(
                [
                    "[plugins]",
                    'path = "not-an-exclude/**"',
                    "",
                    "[[smells.exclude]]",
                    "# a comment inside the block",
                    'path = "scripts/legacy/**"',
                    'path = "after-the-first-path/**"',
                    "[[smells.exclude]]",
                    "[other]",
                    'path = "after-another-table/**"',
                    "[[smells.exclude]]",
                    "path = unquoted",
                    "[[smells.exclude]]",
                    'path   =   "tests/**"',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.assertEqual(qlty._load_smells_exclude_patterns(self.repo), ["scripts/legacy/**", "tests/**"])

    def test_extract_path_pattern(self) -> None:
        """Only a quoted `path = "..."` value is a pattern."""
        self.assertEqual(qlty._extract_path_pattern('path = "a/**"'), "a/**")
        self.assertIsNone(qlty._extract_path_pattern("path = a/**"))

    def test_findings_under_an_excluded_path_are_dropped(self) -> None:
        """A file header matching a pattern drops its whole indented block; others stay."""
        output = "\n".join(
            [
                "scripts/legacy/old.py",
                "  complexity 31 in foo",
                "src/app.py",
                "  complexity 26 in bar",
            ]
        )
        self.assertEqual(qlty._filter_smells_output(output, ["scripts/legacy/*"]), "src/app.py\n  complexity 26 in bar")
        self.assertEqual(qlty._filter_smells_output(output, []), output)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
