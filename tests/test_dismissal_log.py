"""The dismissal log is the audit trail behind "0 alerts"; keep it well-formed."""

from __future__ import absolute_import

import datetime as dt
import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
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

    def test_no_logged_dismissal_can_disappear(self) -> None:
        """The receipt only grows: every alert logged so far must stay logged."""
        logged = set(re.findall(r"^\| \[#(\d+)\]", DOC.read_text(encoding="utf-8"), flags=re.MULTILINE))
        recorded = {
            "25", "26", "27", "28", "29", "30", "31", "32", "57", "82", "100", "109", "110", "111", "112",
            "113", "114", "126", "127", "128", "129", "138", "139", "166", "167", "168", "169", "173",
            "245", "246", "247", "272", "273", "297", "298", "299", "300", "301", "302",
        }  # fmt: skip
        self.assertEqual(recorded - logged, set())

    def test_a_well_formed_row_passes(self) -> None:
        """Including a pending won't-fix with no owner yet, and an escaped pipe in a cell."""
        pending = _row("8", reason="won't fix", dismissed="2026-04-27", verdict="**Re-review.** wrong category")
        spaced = _row("11", location="`fixtures/my case.py:12`")
        piped = _row("9", rationale="a \\| b")
        accepted = _row("10", reason="won't fix", owner="@owner", review_by="2027-01-01")
        aligned = _doc(_row(), separator="| :--- | ---: | :---: | --- | --- | --- | --- | --- | --- |")
        self.assertEqual(_check(_doc(_row(), pending, piped, accepted, spaced)), [])
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
            _row(verdict=""): "verdict is empty",
            _row(reason="won't fix", review_by="2027-01-01"): "names no owner",
            _row(reason="won't fix", owner="@owner"): "has no real review-by date",
            _row(reason="won't fix", owner="@owner", review_by="2027-02-30"): "has no real review-by date",
            _row(reason="won't fix", owner="@owner", review_by="2020-01-01"): "is not after its dismissal",
            _row(reason="won't fix", owner="@owner", dismissed="2026-01-02", review_by="2026-06-30"): (
                "was due for review on 2026-06-30"
            ),
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
        self.assertIn("after the log table's end", problem)

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
        self.assertIn("excluded query 'py/undocumented' has no row", err.getvalue())
        self.assertIn("--repo is required", err.getvalue())

    def test_every_query_exclusion_is_a_documented_decision(self) -> None:
        """Rule 4: a global query filter cannot land without its row in the decisions table."""
        text = DOC.read_text(encoding="utf-8")
        config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        self.assertTrue(config["query-filters"])  # the reconciliation below is not vacuous
        self.assertEqual(log.undocumented_exclusions(config, text), [])
        decisions = "## Query configuration decisions\n\n| Query | Decided | Decision |\n| --- | --- | --- |\n"
        decisions += "| `py/kept`, `py/also` | 2026-10-24 | reason |\n\n## Log\n\n| `py/not-a-decision` |\n"
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
                "excluded query 'py/new' has no row under '## Query configuration decisions'",
                "excluded query 'py/not-a-decision' has no row under '## Query configuration decisions'",
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
        for broken in ("not json", "[]"):
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
