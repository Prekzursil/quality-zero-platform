"""Tests for the Phase 4 §5.3 security-class QRv2 guard.

Verifies that synthetic security-class findings (Dependabot-class,
CWE-tagged, scanner-name heuristic, category flag, OWASP reference)
are all pulled out of the auto-merge-eligible set. Also exercises the
``SecurityAutoMergeRefusedError`` belt-and-suspenders error.
"""

from __future__ import absolute_import

import unittest

from scripts.quality import security_class_guard as sg


class IsSecurityFindingTests(unittest.TestCase):
    """``is_security_finding`` detects every documented signal."""

    def test_dependabot_source_is_security(self) -> None:
        """A synthetic Dependabot finding is recognised by its scanner name."""
        self.assertTrue(
            sg.is_security_finding({"scanner": "dependabot", "id": "DEP-1"})
        )

    def test_case_insensitive_scanner_match(self) -> None:
        """Scanner name matching is case-insensitive."""
        self.assertTrue(
            sg.is_security_finding({"scanner": "Semgrep", "id": "sem-1"})
        )

    def test_is_security_flag_overrides(self) -> None:
        """Explicit ``is_security: True`` is enough to flag the finding."""
        self.assertTrue(
            sg.is_security_finding({"scanner": "custom", "is_security": True})
        )

    def test_category_security_flag(self) -> None:
        """``category: security`` (and synonyms) flag the finding."""
        for category in ("security", "Security", "vulnerability", "secret"):
            with self.subTest(category=category):
                self.assertTrue(
                    sg.is_security_finding(
                        {"scanner": "misc", "category": category}
                    )
                )

    def test_cwe_reference_in_id(self) -> None:
        """CWE-<n> in the finding id flags it as security-class."""
        self.assertTrue(
            sg.is_security_finding(
                {"scanner": "custom", "id": "run-shell-injection CWE-78"}
            )
        )

    def test_cwe_reference_in_tags(self) -> None:
        """CWE references inside ``tags`` are also recognised."""
        self.assertTrue(
            sg.is_security_finding(
                {"scanner": "custom", "tags": ["style", "CWE-79"]}
            )
        )

    def test_owasp_reference_in_message(self) -> None:
        """``A01:2021`` style OWASP refs flag the finding."""
        self.assertTrue(
            sg.is_security_finding(
                {"scanner": "custom", "message": "maps to A03:2021 Injection"}
            )
        )

    def test_non_security_finding_returns_false(self) -> None:
        """Style/formatting findings with no security signal → False."""
        self.assertFalse(
            sg.is_security_finding(
                {
                    "scanner": "prettier",
                    "id": "max-line-length",
                    "category": "style",
                }
            )
        )

    def test_non_mapping_input_returns_false(self) -> None:
        """Defensive: ``None`` / lists don't crash."""
        self.assertFalse(sg.is_security_finding(None))  # type: ignore[arg-type]
        self.assertFalse(sg.is_security_finding([]))  # type: ignore[arg-type]

    def test_alternate_scanner_keys_recognised(self) -> None:
        """``source`` / ``tool`` / ``analyzer`` all populate the scanner hint."""
        for key in ("source", "tool", "analyzer"):
            with self.subTest(key=key):
                self.assertTrue(sg.is_security_finding({key: "codeql"}))

    def test_no_scanner_hint_no_tags_returns_false(self) -> None:
        """Finding without any scanner key or security signal is non-security."""
        self.assertFalse(sg.is_security_finding({"id": "style-001"}))


class FilterAutoMergeCandidatesTests(unittest.TestCase):
    """``filter_auto_merge_candidates`` splits into two disjoint lists."""

    def test_security_findings_go_to_pr_list(self) -> None:
        """Dependabot + CWE entries land in ``must_open_pr``."""
        findings = [
            {"scanner": "dependabot", "id": "DEP-1"},
            {"scanner": "eslint", "id": "no-unused-vars"},
            {"scanner": "custom", "id": "CWE-78 path"},
        ]
        result = sg.filter_auto_merge_candidates(findings)
        self.assertEqual(len(result.must_open_pr), 2)
        self.assertEqual(len(result.auto_merge_ok), 1)
        self.assertEqual(result.auto_merge_ok[0]["id"], "no-unused-vars")


class EnsurePrOnlyGuardTests(unittest.TestCase):
    """``ensure_pr_only_for_security`` refuses auto-merge for security findings."""

    def test_no_auto_merge_intent_is_silent(self) -> None:
        """When caller doesn't intend auto-merge, the guard never raises."""
        sg.ensure_pr_only_for_security(
            [{"scanner": "dependabot"}], intends_auto_merge=False,
        )  # no exception expected

    def test_auto_merge_with_security_raises(self) -> None:
        """A Dependabot-class finding blocks auto-merge."""
        with self.assertRaises(sg.SecurityAutoMergeRefusedError) as ctx:
            sg.ensure_pr_only_for_security(
                [{"scanner": "dependabot", "id": "DEP-42"}],
                intends_auto_merge=True,
            )
        self.assertIn("DEP-42", str(ctx.exception))

    def test_auto_merge_without_security_passes(self) -> None:
        """Style-only findings don't trip the guard."""
        sg.ensure_pr_only_for_security(
            [
                {"scanner": "eslint", "id": "no-unused-vars"},
                {"scanner": "prettier", "id": "max-line-length"},
            ],
            intends_auto_merge=True,
        )  # no exception expected

    def test_refusal_message_names_every_security_finding(self) -> None:
        """The error message lists each finding's id so logs stay actionable."""
        with self.assertRaises(sg.SecurityAutoMergeRefusedError) as ctx:
            sg.ensure_pr_only_for_security(
                [
                    {"scanner": "codeql", "id": "py/injection"},
                    {"scanner": "semgrep", "id": "xss.reflected"},
                ],
                intends_auto_merge=True,
            )
        msg = str(ctx.exception)
        self.assertIn("py/injection", msg)
        self.assertIn("xss.reflected", msg)

    def test_dependabot_synthetic_finding_blocks_auto_merge(self) -> None:
        """Phase 4 ABSOLUTE DONE contract: synthetic Dependabot finding forces PR.

        This is the explicit acceptance check from the loop prompt
        ("Security-class QRv2 fixes always open a PR — verified with
        a synthetic Dependabot-class finding").
        """
        synthetic = {
            "scanner": "dependabot",
            "id": "GHSA-xxxx-yyyy-zzzz",
            "category": "security",
            "is_security": True,
            "message": "Regular Expression Denial of Service (ReDoS) (CWE-1333)",
        }
        classified = sg.filter_auto_merge_candidates([synthetic])
        self.assertEqual(len(classified.must_open_pr), 1)
        self.assertEqual(classified.auto_merge_ok, [])
        with self.assertRaises(sg.SecurityAutoMergeRefusedError):
            sg.ensure_pr_only_for_security([synthetic], intends_auto_merge=True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
