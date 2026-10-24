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
REPO = "Prekzursil/quality-zero-platform"
LINK = f"https://github.com/{REPO}/security/code-scanning"
HEADER = (
    "| Alert | Rule | Location (at dismissal) | Reason | Dismissed | Rationale (as recorded) "
    "| Owner | Review by | Verdict under this rule |"
)
RULE = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"


def _row(
    number: str = "7",
    *,
    link: str = "",
    rule: str = "`py/x`",
    location: str = "`a.py:1`",
    reason: str = "false positive",
    dismissed: str = "2026-10-24",
    rationale: str = "the flow cannot happen: the input is a constant",
    owner: str = "-",
    review_by: str = "-",
    verdict: str = "Kept.",
) -> str:
    return (
        f"| [#{number}]({link or LINK + '/' + number}) | {rule} | {location} | {reason} | {dismissed} "
        f"| {rationale} | {owner} | {review_by} | {verdict} |"
    )


def _doc(*rows: str, header: str = HEADER, separator: str = RULE) -> str:
    return "\n".join(["# Doc", "", "## Log (repo)", "", header, separator, *rows, ""])


class DismissalLogTests(unittest.TestCase):
    """validate() accepts the committed log and names every malformation."""

    def test_the_committed_log_is_well_formed(self) -> None:
        """The QZP log itself passes, so CI catches any drift in it."""
        self.assertEqual(log.validate(DOC.read_text(encoding="utf-8"), repo=REPO), [])

    def test_a_well_formed_row_passes(self) -> None:
        """Including a pending won't-fix with no owner yet, and an escaped pipe in a cell."""
        pending = _row("8", reason="won't fix", verdict="**Re-review.** wrong category")
        piped = _row("9", rationale="a \\| b")
        accepted = _row("10", reason="won't fix", owner="@owner", review_by="2027-01-01")
        aligned = _doc(_row(), separator="| :--- | ---: | :---: | --- | --- | --- | --- | --- | --- |")
        self.assertEqual(log.validate(_doc(_row(), pending, piped, accepted), repo=REPO), [])
        self.assertEqual(log.validate(aligned, repo=REPO), [])

    def test_each_malformation_is_named(self) -> None:
        """One broken field per row, each reported once."""
        cases = {
            _row(link=f"{LINK}/8"): "is not a link to its own alert number",
            _row(link="https://github.com/other/repo/security/code-scanning/7"): f"alert number in {REPO}",
            _row(rule="py/x"): "is not a backticked query id",
            _row(location=""): "is not a backticked path:line",
            _row(location="`a.py`"): "is not a backticked path:line",
            _row(reason="wontfix"): "is not one of",
            _row(dismissed="24/10/2026"): "is not a real YYYY-MM-DD date",
            _row(dismissed="2026-99-99"): "is not a real YYYY-MM-DD date",
            _row(rationale=""): "rationale is empty",
            _row(verdict=""): "verdict is empty",
            _row(reason="won't fix", review_by="2027-01-01"): "names no owner",
            _row(reason="won't fix", owner="@owner"): "has no real review-by date",
            _row(reason="won't fix", owner="@owner", review_by="2027-02-30"): "has no real review-by date",
            "| [#7](x) | too | few |": "3 cells, expected 9",
        }
        for row, expected in cases.items():
            with self.subTest(expected=expected):
                (problem,) = log.validate(_doc(row), repo=REPO)
                self.assertIn(expected, problem)
                self.assertTrue(problem.startswith("row 1: "))

    def test_a_duplicate_alert_is_rejected(self) -> None:
        """Two rows for one alert would let a dismissal be rewritten silently."""
        self.assertEqual(log.validate(_doc(_row(), _row()), repo=REPO), ["row 2: alert #7 is logged twice"])

    def test_a_missing_section_columns_or_separator_is_rejected(self) -> None:
        """Without the agreed table there is nothing to audit; a row in the separator slot would skip every check."""
        self.assertEqual(log.validate("# Doc\n\nno log here\n", repo=REPO), ["no '## Log ...' section"])
        (problem,) = log.validate(_doc(_row(), header="| Alert | Rule |"), repo=REPO)
        self.assertIn("columns must be exactly", problem)
        (problem,) = log.validate("## Log\n\nprose only\n", repo=REPO)
        self.assertIn("columns must be exactly", problem)
        for separator in (_row(dismissed="2026-99-99"), "| --- | --- |"):
            with self.subTest(separator=separator):
                (problem,) = log.validate(_doc(separator=separator), repo=REPO)
                self.assertIn("second line must be its |---| separator", problem)

    def test_the_cli_exits_by_the_log_state(self) -> None:
        """0 for the committed log; 1 with every problem listed for a broken one."""
        self.assertEqual(log.main([str(DOC), "--repo", REPO]), 0)
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "log.md"
            bad.write_text(_doc(_row(reason="nope")), encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(log.main([str(bad), "--repo", REPO]), 1)
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
