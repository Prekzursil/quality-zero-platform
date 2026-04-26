"""Tests for ``scripts.quality.bump_workflow_shas`` — fleet-wide reusable-workflow SHA bumper.

Discovered by auditing the QZP fleet 2026-04-26: 14 of 14 consumer
repos pin pre-Phase-2 reusable-workflow SHAs. Drift-sync renders new
content for codecov.yml / ci.yml / etc but does NOT touch the
caller workflows that pin platform SHAs. This module is the pure
logic that walks workflow text, identifies QZP reusable-workflow
``uses:`` lines, and bumps each SHA to a target.
"""

from __future__ import absolute_import

import unittest

from scripts.quality import bump_workflow_shas as bws


class FindReusablePinsTests(unittest.TestCase):
    """``find_reusable_pins`` extracts (name, sha) tuples from workflow text."""

    def test_extracts_single_pin(self) -> None:
        """One ``uses:`` line → one pin."""
        text = (
            "    uses: Prekzursil/quality-zero-platform/.github/workflows/"
            "reusable-codecov-analytics.yml@cc7e3095598478230cd85566a72e310acd1a8923\n"
        )
        pins = bws.find_reusable_pins(text)
        self.assertEqual(
            pins,
            [("reusable-codecov-analytics.yml",
              "cc7e3095598478230cd85566a72e310acd1a8923")],
        )

    def test_extracts_multiple_pins_in_order(self) -> None:
        """Multiple ``uses:`` references → preserved in document order."""
        text = (
            "uses: Prekzursil/quality-zero-platform/.github/workflows/"
            "reusable-codeql.yml@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
            "uses: Prekzursil/quality-zero-platform/.github/workflows/"
            "reusable-scanner-matrix.yml@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
        )
        pins = bws.find_reusable_pins(text)
        self.assertEqual(len(pins), 2)
        self.assertEqual(pins[0][0], "reusable-codeql.yml")
        self.assertEqual(pins[1][0], "reusable-scanner-matrix.yml")

    def test_ignores_other_repo_workflows(self) -> None:
        """Pins on workflows from OTHER repos are NOT touched."""
        text = (
            "uses: actions/checkout@v4\n"
            "uses: SomeOtherOrg/other-repo/.github/workflows/x.yml@"
            "1234567890123456789012345678901234567890\n"
        )
        self.assertEqual(bws.find_reusable_pins(text), [])

    def test_ignores_branch_or_tag_pins(self) -> None:
        """Only 40-char hex SHAs are considered pins (skip @main / @v1 etc)."""
        text = (
            "uses: Prekzursil/quality-zero-platform/.github/workflows/"
            "reusable-codeql.yml@main\n"
        )
        self.assertEqual(bws.find_reusable_pins(text), [])

    def test_short_sha_is_ignored(self) -> None:
        """Short SHAs (<40 chars) are not full pins — skip them."""
        text = (
            "uses: Prekzursil/quality-zero-platform/.github/workflows/"
            "reusable-codeql.yml@cc7e3095\n"
        )
        self.assertEqual(bws.find_reusable_pins(text), [])


class BumpPinsToTargetTests(unittest.TestCase):
    """``bump_pins_to_target`` rewrites every QZP SHA to ``target_sha``."""

    def test_bumps_every_qzp_pin(self) -> None:
        """All QZP pins move to target; non-QZP lines untouched."""
        old_sha = "cc7e3095598478230cd85566a72e310acd1a8923"
        new_sha = "b24d2cabf9e2244fd4d981f40c91acdac3fd26eb"
        text = (
            "uses: actions/checkout@v4\n"
            f"uses: Prekzursil/quality-zero-platform/.github/workflows/"
            f"reusable-codecov-analytics.yml@{old_sha}\n"
            f"uses: Prekzursil/quality-zero-platform/.github/workflows/"
            f"reusable-codeql.yml@{old_sha}\n"
            "uses: actions/setup-python@v5\n"
        )
        new_text, count = bws.bump_pins_to_target(text, target_sha=new_sha)
        self.assertEqual(count, 2)
        self.assertNotIn(old_sha, new_text)
        self.assertEqual(new_text.count(new_sha), 2)
        # Non-QZP lines preserved verbatim.
        self.assertIn("uses: actions/checkout@v4\n", new_text)
        self.assertIn("uses: actions/setup-python@v5\n", new_text)

    def test_idempotent_when_already_at_target(self) -> None:
        """Running twice leaves text identical + count = 0 the second time."""
        target = "b24d2cabf9e2244fd4d981f40c91acdac3fd26eb"
        text = (
            f"uses: Prekzursil/quality-zero-platform/.github/workflows/"
            f"reusable-codeql.yml@{target}\n"
        )
        new_text, count = bws.bump_pins_to_target(text, target_sha=target)
        self.assertEqual(new_text, text)
        self.assertEqual(count, 0)

    def test_mixed_pins_all_bumped(self) -> None:
        """Different OLD shas → every pin lands on the same NEW sha."""
        new_sha = "b24d2cabf9e2244fd4d981f40c91acdac3fd26eb"
        text = (
            "uses: Prekzursil/quality-zero-platform/.github/workflows/"
            "reusable-codeql.yml@d7a94db4ab57df42940832cf67b730c673af7da6\n"
            "uses: Prekzursil/quality-zero-platform/.github/workflows/"
            "reusable-scanner-matrix.yml@cc7e3095598478230cd85566a72e310acd1a8923\n"
            "uses: Prekzursil/quality-zero-platform/.github/workflows/"
            "reusable-backlog-sweep.yml@7268fee30f1cf796938d97fe460259f27386a8cd\n"
        )
        new_text, count = bws.bump_pins_to_target(text, target_sha=new_sha)
        self.assertEqual(count, 3)
        self.assertEqual(new_text.count(new_sha), 3)

    def test_rejects_non_40_hex_target(self) -> None:
        """Refuse non-SHA targets — protects against pinning a branch name."""
        with self.assertRaises(ValueError):
            bws.bump_pins_to_target("", target_sha="main")
        with self.assertRaises(ValueError):
            bws.bump_pins_to_target("", target_sha="cc7e3095")  # short
        with self.assertRaises(ValueError):
            bws.bump_pins_to_target("", target_sha="z" * 40)  # not hex


class BumpWorkflowFilesTests(unittest.TestCase):
    """``bump_workflow_files`` walks files + returns per-file change count."""

    def test_rejects_non_sha_target(self) -> None:
        """``bump_workflow_files`` rejects non-40-char-hex targets too."""
        with self.assertRaises(ValueError):
            bws.bump_workflow_files({"x.yml": "anything"}, target_sha="main")

    def test_returns_per_file_change_counts(self) -> None:
        """Each path → integer count of pins bumped (0 if absent or already current)."""
        target = "b24d2cabf9e2244fd4d981f40c91acdac3fd26eb"
        files = {
            "codecov-analytics.yml": (
                "uses: Prekzursil/quality-zero-platform/.github/workflows/"
                "reusable-codecov-analytics.yml@cc7e3095598478230cd85566a72e310acd1a8923\n"
            ),
            "ci.yml": "uses: actions/checkout@v4\n",  # no QZP pin
            "codeql.yml": (
                "uses: Prekzursil/quality-zero-platform/.github/workflows/"
                f"reusable-codeql.yml@{target}\n"  # already current
            ),
        }
        results = bws.bump_workflow_files(files, target_sha=target)
        self.assertEqual(results["codecov-analytics.yml"]["bumped"], 1)
        self.assertEqual(results["ci.yml"]["bumped"], 0)
        self.assertEqual(results["codeql.yml"]["bumped"], 0)
        # New text only present where bumps happened.
        self.assertIn(target, results["codecov-analytics.yml"]["new_text"])
        # Untouched files have new_text == input.
        self.assertEqual(results["ci.yml"]["new_text"], files["ci.yml"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
