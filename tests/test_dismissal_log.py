"""The dismissal log is the audit trail behind "0 alerts"; keep it well-formed."""

from __future__ import absolute_import

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from scripts.quality import check_dismissal_log as log

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "CODE-SCANNING-DISMISSALS.md"
LINK = "https://github.com/Prekzursil/quality-zero-platform/security/code-scanning"
HEADER = (
    "| Alert | Rule | Location (at dismissal) | Reason | Dismissed | Rationale (as recorded) "
    "| Owner | Review by | Verdict under this rule |"
)
RULE = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"


def _row(
    number: str = "7",
    *,
    link_number: str = "",
    rule: str = "`py/x`",
    reason: str = "false positive",
    dismissed: str = "2026-10-24",
    rationale: str = "the flow cannot happen: the input is a constant",
    owner: str = "-",
    review_by: str = "-",
    verdict: str = "Kept.",
) -> str:
    return (
        f"| [#{number}]({LINK}/{link_number or number}) | {rule} | `a.py:1` | {reason} | {dismissed} "
        f"| {rationale} | {owner} | {review_by} | {verdict} |"
    )


def _doc(*rows: str, header: str = HEADER) -> str:
    return "\n".join(["# Doc", "", "## Log (repo)", "", header, RULE, *rows, ""])


class DismissalLogTests(unittest.TestCase):
    """validate() accepts the committed log and names every malformation."""

    def test_the_committed_log_is_well_formed(self) -> None:
        """The QZP log itself passes, so CI catches any drift in it."""
        self.assertEqual(log.validate(DOC.read_text(encoding="utf-8")), [])

    def test_a_well_formed_row_passes(self) -> None:
        """Including a pending won't-fix with no owner yet, and an escaped pipe in a cell."""
        pending = _row("8", reason="won't fix", verdict="**Re-review.** wrong category")
        piped = _row("9", rationale="a \\| b")
        accepted = _row("10", reason="won't fix", owner="@owner", review_by="2027-01-01")
        self.assertEqual(log.validate(_doc(_row(), pending, piped, accepted)), [])

    def test_each_malformation_is_named(self) -> None:
        """One broken field per row, each reported once."""
        cases = {
            _row(link_number="8"): "is not a link to its own alert number",
            _row(rule="py/x"): "is not a backticked query id",
            _row(reason="wontfix"): "is not one of",
            _row(dismissed="24/10/2026"): "is not YYYY-MM-DD",
            _row(rationale=""): "rationale is empty",
            _row(verdict=""): "verdict is empty",
            _row(reason="won't fix", review_by="2027-01-01"): "names no owner",
            _row(reason="won't fix", owner="@owner"): "has no review-by date",
            "| [#7](x) | too | few |": "3 cells, expected 9",
        }
        for row, expected in cases.items():
            with self.subTest(expected=expected):
                (problem,) = log.validate(_doc(row))
                self.assertIn(expected, problem)
                self.assertTrue(problem.startswith("row 1: "))

    def test_a_duplicate_alert_is_rejected(self) -> None:
        """Two rows for one alert would let a dismissal be rewritten silently."""
        self.assertEqual(log.validate(_doc(_row(), _row())), ["row 2: alert #7 is logged twice"])

    def test_a_missing_section_or_wrong_columns_is_rejected(self) -> None:
        """Without the agreed table there is nothing to audit."""
        self.assertEqual(log.validate("# Doc\n\nno log here\n"), ["no '## Log ...' section"])
        (problem,) = log.validate(_doc(_row(), header="| Alert | Rule |"))
        self.assertIn("columns must be exactly", problem)
        (problem,) = log.validate("## Log\n\nprose only\n")
        self.assertIn("columns must be exactly", problem)

    def test_the_cli_exits_by_the_log_state(self) -> None:
        """0 for the committed log; 1 with every problem listed for a broken one."""
        self.assertEqual(log.main([str(DOC)]), 0)
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "log.md"
            bad.write_text(_doc(_row(reason="nope")), encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(log.main([str(bad)]), 1)
        self.assertIn("reason 'nope' is not one of", err.getvalue())

    def test_the_documented_comment_budget_adds_up(self) -> None:
        """The 238-character core plus the log pointer is exactly GitHub's 280-character cap."""
        text = DOC.read_text(encoding="utf-8")
        suffix = ". Logged: docs/CODE-SCANNING-DISMISSALS.md"
        self.assertIn(f'dismissed_comment="<rationale core>{suffix}"', text)
        self.assertIn("**238\ncharacters or fewer**", text)
        self.assertEqual(238 + len(suffix), 280)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
