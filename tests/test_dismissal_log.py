"""The dismissal log is the audit trail behind "0 alerts"; keep it well-formed."""

from __future__ import absolute_import

import ast
import datetime as dt
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

from scripts.quality import check_dismissal_log as log

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "CODE-SCANNING-DISMISSALS.md"
CONFIG = ROOT / ".github" / "codeql" / "codeql-config.yml"
SEMGREP = ROOT / "tests" / "quality" / "rollup_v2" / "fixtures" / "normalizers" / "semgrep_sample.sarif.json"
REPO = "Prekzursil/quality-zero-platform"
LINK = f"https://github.com/{REPO}/security/code-scanning"
HEADER = (
    "| Alert | Rule | Location (at dismissal) | Reason | Dismissed | Rationale (as recorded) "
    "| Owner | Review by | Verdict under this rule |"
)
RULE = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
TODAY = dt.date(2026, 10, 24)


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


def _check(text: str) -> list:
    """Validate a synthetic log on a fixed date, so no fixture's review-by ever expires."""
    return log.validate(text, repo=REPO, today=TODAY)


class DismissalLogTests(unittest.TestCase):
    """validate() accepts the committed log and names every malformation."""

    def test_the_committed_log_is_well_formed(self) -> None:
        """The QZP log itself passes, so CI catches any drift in it."""
        self.assertEqual(log.validate(DOC.read_text(encoding="utf-8"), repo=REPO), [])

    def test_the_log_is_append_only_against_this_baseline(self) -> None:
        """The receipt only grows: a row can never leave the VALIDATED log table.

        Review finding: a later row whose alert leaves GitHub's dismissed set
        (fixed, say) could be deleted unnoticed. So the baseline must equal the
        log: a new row is added HERE in the same PR, and removing one is an
        explicit, reviewable edit of this test.
        """
        logged = set(log.logged_alerts(DOC.read_text(encoding="utf-8")))
        recorded = {
            "25", "26", "27", "28", "29", "30", "31", "32", "57", "82", "100", "109", "110", "111", "112",
            "113", "114", "126", "127", "128", "129", "138", "139", "166", "167", "168", "169", "173",
            "245", "246", "247", "272", "273", "297", "298", "299", "300", "301", "302",
            "306", "307", "308", "309", "310", "312", "313", "314", "315", "316", "317",
        }  # fmt: skip
        self.assertEqual(recorded - logged, set(), "a logged dismissal disappeared")
        self.assertEqual(logged - recorded, set(), "add each new log row's alert number to this baseline")

    def test_a_well_formed_row_passes(self) -> None:
        """Including a pending won't-fix with no owner yet, and an escaped pipe in a cell."""
        pending = _row("8", reason="won't fix", dismissed="2026-04-27", verdict="**Re-review.** wrong category")
        spaced = _row("11", location="`fixtures/my case.py:12`")
        expiring = _row("12", owner="Prekzursil", review_by="2027-01-24", verdict="Kept until CodeQL models it.")
        piped = _row("9", rationale="a \\| b")
        accepted = _row("10", reason="won't fix", owner="@owner", review_by="2027-01-01")
        aligned = _doc(_row(), separator="| :--- | ---: | :---: | --- | --- | --- | --- | --- | --- |")
        self.assertEqual(_check(_doc(_row(), pending, piped, accepted, spaced, expiring)), [])
        self.assertEqual(_check(aligned), [])

    def test_each_malformation_is_named(self) -> None:
        """One broken field per row, each reported once."""
        cases = {
            _row(link=f"{LINK}/8"): "is not a link to its own alert number",
            _row(link="https://github.com/other/repo/security/code-scanning/7"): f"alert number in {REPO}",
            _row(rule="py/x"): "is not a backticked query id",
            _row(rule="` `"): "is not a backticked query id",
            _row(rule="` py/x`"): "is not a backticked query id",
            _row(location=""): "is not a backticked path:line",
            _row(location="`a.py`"): "is not a backticked path:line",
            _row(reason="wontfix"): "is not one of",
            _row(dismissed="24/10/2026"): "is not a real YYYY-MM-DD date",
            _row(dismissed="2026-99-99"): "is not a real YYYY-MM-DD date",
            _row(dismissed="2099-01-01"): "is in the future",
            _row(rationale=""): "rationale is empty",
            _row(rationale="-"): "rationale is empty",
            _row(verdict=""): "verdict is empty",
            _row(verdict="-"): "verdict is empty",
            _row(reason="won't fix", review_by="2027-01-01"): "names no owner",
            _row(reason="won't fix", owner="@owner"): "has no real review-by date",
            _row(reason="won't fix", owner="@owner", review_by="2027-02-30"): "has no real review-by date",
            _row(reason="won't fix", owner="@owner", review_by="2020-01-01"): "is not after its dismissal",
            _row(reason="won't fix", owner="@owner", dismissed="2026-01-02", review_by="2026-06-30"): (
                "was due for review on 2026-06-30"
            ),
            # Any row's review-by expires: a false positive whose justification can lapse, too.
            _row(dismissed="2026-01-02", review_by="2026-06-30"): "was due for review on 2026-06-30",
            _row(review_by="2026-10-01"): "is not after its dismissal",
            _row(review_by="soon"): "is neither '-' nor a real date",
            _row(reason="won't fix", review_by="2027-01-01", verdict="**Fixed by design.** accepted"): "names no owner",
            # A pending marker exempts only PRE-policy rows; a new won't-fix needs owner + date.
            _row(reason="won't fix", review_by="2027-01-01", verdict="**Re-review.** later"): "names no owner",
            _row(link=f"{LINK}/0", number="0"): "is not a link to its own alert number",
            _row(link=f"{LINK}/07", number="07"): "is not a link to its own alert number",
            "| [#7](x) | too | few |": "3 cells, expected 9",
        }
        for row, expected in cases.items():
            with self.subTest(expected=expected):
                (problem,) = _check(_doc(row))
                self.assertIn(expected, problem)
                self.assertTrue(problem.startswith("row 1: "))

    def test_a_dismissal_date_may_lead_utc_by_one_day_only(self) -> None:
        """A receipt records the past; one day of slack covers a dismisser east of UTC."""
        today = dt.date(2026, 10, 24)
        self.assertEqual(log.validate(_doc(_row(dismissed="2026-10-25")), repo=REPO, today=today), [])
        self.assertEqual(
            log.validate(_doc(_row(dismissed="2026-10-26")), repo=REPO, today=today),
            ["row 1: dismissed date 2026-10-26 is in the future"],
        )

    def test_a_row_without_its_leading_pipe_is_reported_not_skipped(self) -> None:
        """An unpiped or indented row inside the table cannot slip past every check."""
        stray = _row(dismissed="2026-99-99").lstrip("|").lstrip()
        (problem,) = _check(_doc(_row(), stray))
        self.assertIn("not a |-delimited table row", problem)
        indented = "   " + _row("9")
        self.assertEqual(_check(_doc(_row(), indented)), [])

    def test_the_table_ends_at_its_first_blank_line(self) -> None:
        """A row a blank line cut off is reported; prose and later sections are not rows."""
        self.assertEqual(_check(_doc(_row()) + "\nA closing note.\n"), [])
        later = _doc(_row()) + "\n## Next\n\n| other | table |\n| --- | --- |\n"
        self.assertEqual(_check(later), [])
        (problem,) = _check(_doc(_row()) + "\n" + _row("8") + "\n")
        self.assertIn("a row outside the log table", problem)

    def test_no_row_hides_outside_the_table(self) -> None:
        """Review finding: an unpiped row after the blank line, or an alert row before the table, skipped every check."""
        unpiped = _row("8", dismissed="2026-99-99").lstrip("|").lstrip()
        for text in (
            _doc(_row()) + "\n" + unpiped + "\n",  # cut off by a blank line AND missing its pipe
            _doc(_row()).replace("## Log (repo)\n", "## Log (repo)\n\n" + unpiped + "\n"),  # before the header
            _doc(_row()) + "\nA note | with two | pipes\n",  # cells without a leading pipe
        ):
            with self.subTest(text=text[-60:]):
                (problem,) = _check(text)
                self.assertIn("a row outside the log table", problem)
        self.assertEqual(_check(_doc(_row()) + "\nA closing note with one | pipe.\n"), [])

    def test_a_second_log_section_is_rejected(self) -> None:
        """Review finding: rows under a second '## Log' heading were never read."""
        second = _doc(_row()) + "\n## Log (again)\n\n" + HEADER + "\n" + RULE + "\n" + _row("8") + "\n"
        self.assertEqual(
            _check(second), ["2 '## Log' sections: keep one, or the rows under the others skip every check"]
        )

    def test_the_log_heading_is_matched_whole(self) -> None:
        """A '## Logging guidance' section before the log is not the log."""
        self.assertEqual(_check("## Logging guidance\n\nKeep logs short.\n\n" + _doc(_row())), [])
        self.assertEqual(_check(_doc(_row()).replace("## Log (repo)", "## Log")), [])
        (problem,) = _check(_doc(_row()).replace("## Log (repo)", "## Log retention"))
        self.assertIn("no '## Log'", problem)

    def test_an_accepted_risk_is_due_on_its_review_by_date_not_after(self) -> None:
        """The review-by date is the CI trigger: on it the row passes, the day after it fails."""
        due = _doc(_row(reason="won't fix", owner="@owner", dismissed="2026-01-02", review_by="2026-10-24"))
        self.assertEqual(log.validate(due, repo=REPO, today=dt.date(2026, 10, 24)), [])
        (problem,) = log.validate(due, repo=REPO, today=dt.date(2026, 10, 25))
        self.assertIn("was due for review on 2026-10-24", problem)

    def test_a_duplicate_alert_is_rejected(self) -> None:
        """Two rows for one alert would let a dismissal be rewritten silently."""
        self.assertEqual(_check(_doc(_row(), _row())), ["row 2: alert #7 is logged twice"])

    def test_a_missing_section_columns_or_separator_is_rejected(self) -> None:
        """Without the agreed table there is nothing to audit; a row in the separator slot would skip every check."""
        self.assertEqual(_check("# Doc\n\nno log here\n"), ["no '## Log' or '## Log (<repo>)' section"])
        (problem,) = _check(_doc(_row(), header="| Alert | Rule |"))
        self.assertIn("columns must be exactly", problem)
        (problem,) = _check("## Log\n\nprose only\n")
        self.assertIn("columns must be exactly", problem)
        for separator in (_row(dismissed="2026-99-99"), "| --- | --- |"):
            with self.subTest(separator=separator):
                (problem,) = _check(_doc(separator=separator))
                self.assertIn("second line must be its |---| separator", problem)

    def test_the_cli_exits_by_the_log_state(self) -> None:
        """0 for the committed log and config; 1 with every problem listed for a broken one."""
        self.assertEqual(log.main([str(DOC), "--repo", REPO, "--codeql-config", str(CONFIG)]), 0)
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "log.md"
            bad.write_text(_doc(_row(reason="nope")), encoding="utf-8")
            config = Path(tmp) / "codeql-config.yml"
            config.write_text("query-filters:\n  - exclude:\n      id: py/undocumented\n", encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(log.main([str(bad), "--repo", REPO, "--codeql-config", str(config)]), 1)
                absent = str(Path(tmp) / "absent.yml")
                self.assertEqual(log.main([str(DOC), "--repo", REPO, "--codeql-config", absent]), 0)
                with self.assertRaises(SystemExit):
                    log.main([str(DOC)])
        self.assertIn("reason 'nope' is not one of", err.getvalue())
        self.assertIn("excluded query 'py/undocumented' has no '**Excluded...' decision", err.getvalue())
        self.assertIn("--repo is required", err.getvalue())

    def test_preserved_alerts_are_read_from_the_validated_log_only(self) -> None:
        """Review finding: an '| [#25]' line under a later '## Archive' section still counted as logged."""
        archived = _doc(_row()) + "\n## Archive\n\n| [#25](x) | moved here |\n"
        self.assertEqual(log.logged_alerts(archived), ["7"])
        self.assertEqual(log.logged_alerts(_doc(_row(), _row("8"))), ["7", "8"])
        self.assertEqual(log.logged_alerts("# Doc\n\nno log\n"), [])
        self.assertEqual(log.logged_alerts(_doc(_row(), header="| Alert | Rule |")), [])

    def test_a_fenced_example_is_not_the_document(self) -> None:
        """Review finding: a '## Log' table inside a code fence satisfied (or duplicated) the real log."""
        example = "```markdown\n## Log (example)\n\n" + HEADER + "\n" + RULE + "\n" + _row("8") + "\n\n```\n\n"
        self.assertEqual(_check(example + _doc(_row())), [])  # the real log is read, not a duplicate
        self.assertEqual(_check("# Doc\n\n" + example), ["no '## Log' or '## Log (<repo>)' section"])
        tilde = "~~~~\n## Log\n~~~\nstill inside\n~~~~\n\n"
        self.assertEqual(_check(tilde + _doc(_row())), [])
        self.assertEqual(
            log._unfenced(["a", "```", "## Log", "``` trailing", "x", "```", "b"]), ["a", "", "", "", "", "", "b"]
        )

    def test_an_html_comment_is_not_the_document(self) -> None:
        """Review finding: a log or decision hidden in <!-- --> satisfied the contract while nothing rendered."""
        hidden = "<!--\n## Log (sample)\n\n" + HEADER + "\n" + RULE + "\n" + _row("8") + "\n-->\n\n"
        self.assertEqual(_check(hidden + _doc(_row())), [])  # the rendered log is read, not a duplicate
        self.assertEqual(_check("# Doc\n\n" + hidden), ["no '## Log' or '## Log (<repo>)' section"])
        self.assertEqual(
            _check("# Doc\n\n<!-- never closed\n" + _doc(_row())), ["no '## Log' or '## Log (<repo>)' section"]
        )
        self.assertEqual(log._rendered(["a <!-- x --> b", "c<!--", "d", "-->e"]), ["a  b", "c", "", "e"])
        decision = "## Query configuration decisions\n\n<!--\n| Query | Decided | Decision |\n| --- | --- | --- |\n"
        decision += "| `py/x` | 2026-10-24 | **Excluded.** |\n-->\n"
        self.assertEqual(log.documented_queries(decision), set())

    def test_the_decisions_table_is_validated_before_its_ids_count(self) -> None:
        """Review finding: a one-cell pipe row under the heading 'documented' an exclusion."""
        text = DOC.read_text(encoding="utf-8")
        self.assertEqual(log.decision_problems(text), [])
        self.assertEqual(log.documented_queries(text), set())  # no global exclusion is left
        self.assertEqual(log.decision_problems("# Doc\n\nno decisions here\n"), [])

        def decisions(*rows: str, header: str = "| Query | Decided | Decision |") -> str:
            return "\n".join(["## Query configuration decisions", "", header, "| --- | --- | --- |", *rows, ""])

        good = "| `py/a`, `py/b` | before 2026-10-24 | **Excluded (pre-policy).** Review by 2099-12-31. |"
        self.assertEqual(log.documented_queries(decisions(good)), {"py/a", "py/b"})
        # Review finding: a decision NOT to exclude must not authorize a later exclusion.
        refused = "| `py/c` | 2026-10-24 | **Fixed in the code by #1, not excluded.** |"
        self.assertEqual(log.decision_problems(decisions(good, refused)), [])
        self.assertEqual(log.documented_queries(decisions(good, refused)), {"py/a", "py/b"})
        cases = {
            "| `py/x` |": "1 cells, expected 3",
            "| py/x | 2026-10-24 | why |": "is not a comma-separated list of backticked query ids",
            "| `py/x` `py/y` | 2026-10-24 | why |": "is not a comma-separated list of backticked query ids",
            "| `py/x` | soon | why |": "is neither a real YYYY-MM-DD date nor 'before YYYY-MM-DD'",
            "| `py/x` | 2026-02-30 | why |": "is neither a real YYYY-MM-DD date nor 'before YYYY-MM-DD'",
            "| `py/x` | 2026-10-24 |  |": "decision is empty",
            "| `py/x` | 2026-10-24 | - |": "decision is empty",
            "| `py/x` | 2099-01-01 | **Excluded.** Review by 2099-12-31. |": "is in the future",
            "| `py/x` | before 2099-01-01 | **Excluded.** Review by 2099-12-31. |": "is in the future",
            # Review finding: an exclusion "under re-review" could stay forever; it is time-boxed now.
            "| `py/x` | 2026-10-24 | **Excluded.** pending |": "names no real 'Review by YYYY-MM-DD'",
            "| `py/x` | 2026-01-01 | **Excluded.** Review by 2026-06-30. |": "was due for review on 2026-06-30",
        }
        for row, expected in cases.items():
            with self.subTest(row=row):
                (problem,) = log.decision_problems(decisions(good, row))
                self.assertEqual(problem.split(": ", 1)[0], "decision 2")
                self.assertIn(expected, problem)
                self.assertEqual(log.documented_queries(decisions(good, row)), {"py/a", "py/b"})
        (problem,) = log.decision_problems(decisions(good, header="| Query | Decision |"))
        self.assertIn("decisions table's columns must be exactly", problem)
        self.assertEqual(log.documented_queries(decisions(good, header="| Query | Decision |")), set())
        # Review finding: a stale **Excluded** row must not outvote a later "not excluded" for the same query.
        conflicting = decisions(good, "| `py/a` | 2026-10-24 | **Fixed in the code, not excluded.** |")
        self.assertEqual(
            log.decision_problems(conflicting),
            ["more than one decision for py/a: keep one row per query, updated in place"],
        )
        self.assertEqual(log.documented_queries(conflicting), set())
        twice = decisions(good) + "\n" + decisions(good)
        self.assertEqual(log.decision_problems(twice), ["2 '## Query configuration decisions' sections: keep one"])
        self.assertEqual(log.documented_queries(twice), set())

    def test_the_validator_runs_standalone_from_a_governed_repo(self) -> None:
        """Review finding: the documented command must work outside QZP - stdlib plus PyYAML, fetched at a pin."""
        tree = ast.parse((ROOT / "scripts" / "quality" / "check_dismissal_log.py").read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        }
        imported |= {
            node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertLessEqual(
            imported, {"__future__", "argparse", "datetime", "json", "re", "sys", "pathlib", "typing", "yaml"}
        )
        # Review finding: governed repos run it on Python 3.9/3.10 - no 3.11+ syntax or datetime.UTC.
        source = (ROOT / "scripts" / "quality" / "check_dismissal_log.py").read_text(encoding="utf-8")
        ast.parse(source, feature_version=(3, 9))
        self.assertNotIn("dt.UTC", source)
        text = DOC.read_text(encoding="utf-8")
        raw = "https://raw.githubusercontent.com/Prekzursil/quality-zero-platform/$sha/scripts/quality/check_dismissal_log.py"
        self.assertIn(raw, text)
        # Review finding: the classifier step must use the fetched validator too, not a repo-local path.
        self.assertIn('| python "$qzp_validator" --classify', text)
        self.assertIn('python "$qzp_validator" --repo OWNER/REPO', text)
        self.assertNotIn("| python scripts/quality/check_dismissal_log.py --classify", text)

    def test_a_scheduled_run_makes_an_expired_review_by_date_fail_without_a_commit(self) -> None:
        """Review findings: push/PR-only CI never ran the expiry check, nor saw a UI dismissal without a row."""
        workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "dismissal-log.yml").read_text(encoding="utf-8"))
        self.assertIn("schedule", workflow[True])
        (job,) = workflow["jobs"].values()
        self.assertEqual(job["permissions"], {"contents": "read", "security-events": "read"})
        listing, check = job["steps"][-2:]
        self.assertIn("code-scanning/alerts?state=dismissed", listing["run"])
        self.assertIn("{number, rule: .rule.id, dismissed_reason, dismissed_at, dismissed_comment}", listing["run"])
        self.assertIn("--paginate", listing["run"])
        self.assertIn('> "$RUNNER_TEMP/dismissed.jsonl"', listing["run"])
        self.assertEqual(
            check["run"],
            'python scripts/quality/check_dismissal_log.py --repo "$QZ_REPO" --dismissed-jsonl "$RUNNER_TEMP/dismissed.jsonl"',
        )
        self.assertEqual(check["env"]["QZ_REPO"], "${{ github.repository }}")

    def test_every_dismissal_on_github_has_a_log_row_with_its_reason(self) -> None:
        """Review finding: a dismissal made in the UI without a row is the event the policy exists to prevent."""
        text = _doc(_row(), _row("8", reason="won't fix", owner="@o", review_by="2027-01-01"))
        dismissed = [
            {"number": 7, "dismissed_reason": "false positive"},
            {"number": 8, "dismissed_reason": "false positive"},
            {"number": 9, "dismissed_reason": "used in tests"},
        ]
        self.assertEqual(
            log.unlogged_dismissals(text, dismissed),
            [
                "alert #8 is dismissed on GitHub as 'false positive' but logged as \"won't fix\"",
                "alert #9 is dismissed on GitHub (used in tests) but has no log row",
            ],
        )
        self.assertEqual(log.unlogged_dismissals("# Doc\n", dismissed), [])  # validate() reports the log itself
        self.assertEqual(
            log.unlogged_dismissals(_doc("| [#7](x) | too | few |"), dismissed[:1]),
            ["alert #7 is dismissed on GitHub (false positive) but has no log row"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            listing = Path(tmp) / "dismissed.jsonl"
            listing.write_text('{"number": 99, "dismissed_reason": "false positive"}\n\n', encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                code = log.main(
                    [str(DOC), "--repo", REPO, "--codeql-config", str(CONFIG), "--dismissed-jsonl", str(listing)]
                )
        self.assertEqual(code, 1)
        self.assertIn("alert #99 is dismissed on GitHub (false positive) but has no log row", err.getvalue())

    def test_a_rows_date_and_rationale_must_match_githubs_record(self) -> None:
        """Review findings: an invented pre-policy date, or a rationale never attached on GitHub, passed."""
        rationale = "the flow cannot happen: the input is a constant"
        text = _doc(_row(dismissed="2026-10-24", rationale=rationale))

        def check(**fields: Any) -> list:
            alert = {"number": 7, "dismissed_reason": "false positive", **fields}
            return log.unlogged_dismissals(text, [alert])

        attached = f"{rationale}. Logged: docs/CODE-SCANNING-DISMISSALS.md"
        self.assertEqual(check(dismissed_at="2026-10-24T22:10:00Z", dismissed_comment=attached), [])
        self.assertEqual(check(dismissed_at="2026-10-25T01:00:00Z", dismissed_comment=attached), [])  # time zones
        # Before the rule's first commit (same day), a comment without the pointer is history, not a breach.
        self.assertEqual(check(dismissed_at="2026-10-24T13:26:12Z", dismissed_comment=rationale), [])
        self.assertEqual(
            check(dismissed_at="2026-10-24T22:10:00Z", dismissed_comment=rationale),
            [
                "alert #7 was dismissed under the rule without the log pointer 'Logged: docs/CODE-SCANNING-DISMISSALS.md'"
            ],
        )
        self.assertEqual(check(rule="py/x"), [])
        self.assertEqual(check(rule="py/other"), ["alert #7 is a py/other alert on GitHub but logged as `py/x`"])
        self.assertEqual(check(), [])  # an export without the fields checks the reason only
        self.assertEqual(
            check(dismissed_at="2026-04-27T09:00:00Z"),
            ["alert #7 was dismissed on GitHub on 2026-04-27 but is logged as 2026-10-24"],
        )
        self.assertEqual(
            check(dismissed_at=None), ["alert #7 was dismissed on GitHub on ? but is logged as 2026-10-24"]
        )
        self.assertEqual(
            check(dismissed_comment=None),
            ["alert #7 was dismissed on GitHub without a comment: the logged rationale was never attached"],
        )
        self.assertEqual(
            check(dismissed_comment="won't matter. Logged: docs/CODE-SCANNING-DISMISSALS.md"),
            ["alert #7's GitHub comment does not match its logged rationale"],
        )

    def test_the_tolerance_never_moves_a_dismissal_across_the_rules_start(self) -> None:
        """Review finding: a dismissal under the rule, logged a day early, read as pre-policy."""
        early = _doc(_row(reason="won't fix", dismissed="2026-10-23", verdict="**Re-review.** later"))
        alert = {"number": 7, "dismissed_reason": "won't fix", "dismissed_at": "2026-10-24T17:00:00Z"}
        self.assertEqual(
            log.unlogged_dismissals(early, [alert]),
            ["alert #7 was dismissed under the rule (2026-10-24T17:00:00Z) but is logged before it (2026-10-23)"],
        )
        self.assertEqual(log.POLICY_AT[:10], log.POLICY_DATE)

    def test_a_table_indented_as_a_code_block_is_rejected(self) -> None:
        """Review finding: four leading spaces render the table as code, yet it validated."""
        coded = "\n".join(["# Doc", "", "## Log (repo)", "", "    " + HEADER, "    " + RULE, "    " + _row(), ""])
        (problem,) = _check(coded)
        self.assertIn("indented as a code block", problem)
        decisions = "## Query configuration decisions\n\n    | Query | Decided | Decision |\n    | --- | --- | --- |\n"
        decisions += "    | `py/x` | 2026-10-24 | **Excluded.** Review by 2099-12-31. |\n"
        self.assertEqual(log.documented_queries(decisions), set())

    def test_a_row_without_its_closing_pipe_is_rejected_not_truncated(self) -> None:
        """Review finding: '[1:-1]' turned a verdict '**Re-review.**X' into the exact pending marker."""
        open_row = _row("8", reason="won't fix", dismissed="2026-04-27", verdict="**Re-review.**X").rstrip("|").rstrip()
        (problem,) = _check(_doc(_row(), open_row))
        self.assertIn("without its closing '|'", problem)
        self.assertEqual(log._cells("| a | b"), ["a", "b"])  # the last character survives
        self.assertEqual(log._cells("| a | b \\|"), ["a", "b |"])  # an escaped pipe is content, not a delimiter

    def test_every_query_exclusion_is_a_documented_decision(self) -> None:
        """Rule 4: a global query filter cannot land without its row in the decisions table."""
        text = DOC.read_text(encoding="utf-8")
        config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        # No global exclusion is left (py/clear-text-* was fixed in code); the synthetic configs below keep
        # the reconciliation itself under test.
        self.assertNotIn("query-filters", config)
        self.assertEqual(log.undocumented_exclusions(config, text), [])
        decisions = "## Query configuration decisions\n\n| Query | Decided | Decision |\n| --- | --- | --- |\n"
        decisions += (
            "| `py/kept`, `py/also` | 2026-10-24 | **Excluded.** Review by 2099-12-31. |\n\n## Log\n\n"
            "| `py/not-a-decision` |\n"
        )
        added = {
            "query-filters": [
                {"exclude": {"id": ["py/kept", "py/new"]}},
                {"exclude": {"id": "py/also"}},
                {"exclude": {"id": "py/not-a-decision"}},
                {"exclude": {"tags": "security"}},
                {"include": {"id": "py/extra"}},
                "not-a-filter",
            ]
        }
        self.assertEqual(
            log.undocumented_exclusions(added, decisions),
            [
                "excluded query 'py/new' has no '**Excluded...' decision under '## Query configuration decisions'",
                "excluded query 'py/not-a-decision' has no '**Excluded...' decision under '## Query configuration decisions'",
                "a query filter excludes by 'tags'; exclude by query id so each decision is listed",
            ],
        )
        self.assertEqual(log.undocumented_exclusions(None, decisions), [])  # an empty config file

    def test_a_finding_is_security_by_severity_tag_or_scanner_from_any_tool(self) -> None:
        """Rule 2's security rationale applies to a Semgrep result tagged only security/CWE, not just CodeQL's."""
        sarif = json.loads(SEMGREP.read_text(encoding="utf-8"))
        results = [result for run in sarif["runs"] for result in run["results"]]
        self.assertTrue(results)
        for result in results:
            with self.subTest(rule=result["ruleId"]):
                self.assertNotIn("security-severity", json.dumps(result))
                self.assertTrue(log.is_security_finding(result))
        security = (
            {"rule": {"id": "py/sql-injection", "security_severity_level": "high", "tags": []}},
            {"rule": {"id": "js/xss", "tags": ["external/cwe/cwe-079"]}},
            {"rule": {"id": "r", "tags": ["OWASP-A03:2021 - Injection"]}},
            {"id": "sarif-rule", "properties": {"security-severity": "7.5"}},
            {"rule": {"id": "unpinned", "tags": []}, "tool": {"name": "zizmor"}},
        )
        for finding in security:
            with self.subTest(finding=finding):
                self.assertTrue(log.is_security_finding(finding))
        quality = (
            {"rule": {"id": "py/unused-import", "security_severity_level": None, "tags": ["maintainability"]}},
            {"rule": {"id": "r", "tags": ["securityish", "correctness"]}, "tool": {"name": "CodeQL"}},
            {"rule": "not-a-mapping", "tool": "not-a-mapping"},
        )
        for finding in quality:
            with self.subTest(finding=finding):
                self.assertFalse(log.is_security_finding(finding))

    def test_the_cli_classifies_an_alert_from_stdin(self) -> None:
        """``gh api .../alerts/N | check_dismissal_log.py --classify`` names the rationale rule 2 requires."""
        alerts = {
            json.dumps({"rule": {"tags": ["security"]}}): "security\n",
            json.dumps({"rule": {"tags": ["maintainability"]}}): "correctness\n",
        }
        for alert, expected in alerts.items():
            out = io.StringIO()
            with patch("sys.stdin", io.StringIO(alert)), redirect_stdout(out):
                self.assertEqual(log.main(["--classify"]), 0)
            self.assertEqual(out.getvalue(), expected)
        for broken in ("not json", "[]", '{"message": "Not Found"}'):
            with patch("sys.stdin", io.StringIO(broken)), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                log.main(["--classify"])

    def test_a_fixed_in_code_decision_matches_the_workflows(self) -> None:
        """The policy may only call the platform-checkout alerts fixed while no reusable workflow reads platform_ref."""
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("**Fixed in the code by #336, not excluded.**", text)
        for path in sorted((ROOT / ".github" / "workflows").glob("reusable-*.yml")):
            with self.subTest(workflow=path.name):
                self.assertNotIn("inputs.platform_ref", path.read_text(encoding="utf-8"))

    def test_the_documented_comment_budget_adds_up(self) -> None:
        """The 238-character core plus the log pointer is exactly GitHub's 280-character cap."""
        text = DOC.read_text(encoding="utf-8")
        suffix = ". Logged: docs/CODE-SCANNING-DISMISSALS.md"
        self.assertIn(f'dismissed_comment="<rationale core>{suffix}"', text)
        # The copy-paste command must not pick the GitHub reason for the reader.
        self.assertIn('-f dismissed_reason="<false positive | won\'t fix | used in tests>"', text)
        self.assertNotIn('dismissed_reason="false positive"', text)
        self.assertIn("**238\ncharacters or fewer**", text)
        self.assertEqual(238 + len(suffix), 280)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
