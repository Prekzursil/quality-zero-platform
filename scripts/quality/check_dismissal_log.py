#!/usr/bin/env python3
"""Validate a code-scanning dismissal log (docs/CODE-SCANNING-DISMISSALS.md).

The dismissal rule makes every dismissal auditable. This check keeps the log
itself well-formed so the audit trail cannot drift silently:

* the document has one log section, holding one unbroken table with exactly
  the agreed columns and a real separator row; no row hides outside it;
* every row links an alert OF THIS REPOSITORY, names a backticked rule, a
  ``path:line`` location, a GitHub dismissal reason, a real calendar date that
  is not in the future, a rationale and a verdict, and no alert appears twice;
* a ``won't fix`` row names an owner and a real review-by date (accepted risk
  always has someone answerable and a date), unless it predates the rule and
  is marked for re-review;
* a review-by date, on any row, is after the dismissal and has not passed:
  a dismissal whose justification can expire fails until it is re-reviewed;
* the "Query configuration decisions" table (when present) is well-formed,
  and every query the CodeQL configuration excludes has a well-formed row;
* with ``--dismissed-jsonl`` (GitHub's dismissed alerts), every alert
  dismissed on GitHub has a log row with the same reason: a dismissal made
  in the UI without a row fails.

It runs standalone (Python 3.9+ and PyYAML), so a governed repository can
fetch it at a pinned platform commit.

``--classify`` reads one code-scanning alert (``gh api .../alerts/N``) on stdin
and prints which rationale rule 2 requires for it: ``security`` or
``correctness``.
"""

from __future__ import absolute_import

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

#: ``## Log`` or ``## Log (<repo>)`` exactly; never ``## Logging guidance``.
LOG_HEADING = re.compile(r"^## Log(?: \(.+\))?\s*$")
DECISIONS_HEADING = re.compile(r"^## Query configuration decisions\s*$")
DECISIONS_TITLE = "## Query configuration decisions"
#: A section runs to the next h1/h2 heading (or the end of the file).
SECTION_END = re.compile(r"^#{1,2} ")
#: A code fence (CommonMark: up to three spaces, then three or more backticks or tildes).
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
#: An HTML comment, possibly across lines: hidden text, never part of the rendered receipt.
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
COLUMNS = (
    "Alert",
    "Rule",
    "Location (at dismissal)",
    "Reason",
    "Dismissed",
    "Rationale (as recorded)",
    "Owner",
    "Review by",
    "Verdict under this rule",
)
REASONS = frozenset({"false positive", "won't fix", "used in tests"})
SEPARATOR = re.compile(r"^:?-{3,}:?$")
UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
#: An alert link opening a line: a log row that lost its leading pipe.
ALERT_LINK = re.compile(r"^\[#[0-9]+\]\(")
#: The alert number an Alert cell links.
ALERT_NUMBER = re.compile(r"^\[#([1-9][0-9]*)\]\(")
DECISION_COLUMNS = ("Query", "Decided", "Decision")
#: A decision's date, or ``before YYYY-MM-DD`` for one inherited from before the rule.
DECIDED = re.compile(r"^(?:before )?(\d{4}-\d{2}-\d{2})$")
#: Only a decision that starts with this marker AUTHORIZES excluding its
#: queries; "**Fixed in the code ..., not excluded.**" documents the opposite.
EXCLUDED = "**Excluded"
#: The document's marker for "no value" (Owner, Review by); never a rationale or verdict.
MISSING = ("", "-")
#: One backtick pair around a non-blank id with no padding inside it.
RULE_ID = re.compile(r"^`[^`\s](?:[^`]*[^`\s])?`$")
QUERY_ID = re.compile(r"`([^`\s](?:[^`]*[^`\s])?)`")
QUERY_CELL = re.compile(r"^`[^`\s](?:[^`]*[^`\s])?`(?:, `[^`\s](?:[^`]*[^`\s])?`)*$")
# Backticks make a path with spaces unambiguous; the last ``:N`` is the line.
LOCATION = re.compile(r"^`[^`]*[^`\s][^`]*:[1-9][0-9]*`$")
#: Pending markers (Re-review / Fix) exempt only rows dismissed BEFORE this
#: rule existed; a won't-fix dismissed under the rule needs an owner and date.
POLICY_DATE = "2026-10-24"
# Exact markers: a verdict that merely starts with "**Fix" (e.g. "**Fixed by
# design.**") is an accepted risk and must carry an owner and a date.
PENDING = ("**Re-review.**", "**Fix.**")
#: Security markers across SARIF producers: CodeQL tags ``security`` and
#: ``external/cwe/cwe-N``; Semgrep and others ``security``, ``CWE-N``, ``OWASP-...``.
SECURITY_TAG = re.compile(r"^(?:security$|external/cwe/|cwe-|owasp)", re.IGNORECASE)
#: Scanners whose every finding is a security finding, tags or not.
SECURITY_TOOLS = frozenset({"bandit", "gitleaks", "grype", "osv-scanner", "trivy", "zizmor"})


def _closed(line: str) -> bool:
    """A table row that ends with its own (unescaped) closing pipe."""
    stripped = line.strip()
    return stripped.endswith("|") and not stripped.endswith("\\|")


def _cells(line: str) -> List[str]:
    """Split one markdown table row into stripped cells (escaped pipes stay inside a cell).

    Only real delimiters are removed: a row without its closing pipe keeps
    its last character (``_table`` rejects such a row anyway).
    """
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if _closed(body) else body
    return [cell.replace("\\|", "|").strip() for cell in re.split(r"(?<!\\)\|", body)]


def _is_date(value: str) -> bool:
    """True for a real ``YYYY-MM-DD`` calendar date (not merely that shape)."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _unfenced(lines: Sequence[str]) -> List[str]:
    """``lines`` with every fenced code block blanked: an example in a fence is not the document."""
    out: List[str] = []
    fence = ""
    for line in lines:
        match = FENCE.match(line)
        if not fence:
            if match:
                fence = match.group(1)
            out.append("" if match else line)
            continue
        # A closing fence: the same character, at least as long, nothing after it.
        if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) and not match.group(2).strip():
            fence = ""
        out.append("")
    return out


def _rendered(lines: Sequence[str]) -> List[str]:
    """``lines`` as a reader sees them: code fences and HTML comments blanked, line count unchanged.

    A sample log in a fence, or a table hidden in ``<!-- ... -->``, is not
    the receipt; an unclosed comment hides everything after it.
    """
    text = "\n".join(_unfenced(lines))
    text = HTML_COMMENT.sub(lambda match: "\n" * match.group(0).count("\n"), text)
    start = text.find("<!--")
    if start != -1:
        text = text[:start] + "\n" * text[start:].count("\n")
    return text.split("\n")


def _sections(lines: Sequence[str], heading: re.Pattern[str]) -> List[List[str]]:
    """The lines under EVERY rendered ``heading`` (not in a fence or comment), each up to the next h1/h2 heading."""
    lines = _rendered(lines)
    sections: List[List[str]] = []
    for start, line in enumerate(lines):
        if not heading.match(line):
            continue
        body: List[str] = []
        for following in lines[start + 1 :]:
            if SECTION_END.match(following):
                break
            body.append(following)
        sections.append(body)
    return sections


def _row_like(line: str) -> bool:
    """A line that carries a table row Markdown may not render as one: a pipe row, an alert link, or cells."""
    stripped = line.strip()
    return stripped.startswith("|") or bool(ALERT_LINK.match(stripped)) or len(UNESCAPED_PIPE.findall(stripped)) >= 2


def _table(section: Sequence[str], columns: Sequence[str], name: str) -> Tuple[List[List[str]], List[str]]:
    """``(rows as cells, [])`` for the one unbroken table in ``section``, else ``([], [problem])``.

    The table runs from its header to the first blank line, as Markdown
    renders it. A row that lost its leading pipe, or one outside the table
    (before it, or cut off by a blank line), is reported - never silently
    skipped past the checks.
    """
    columns_problem = [f"the {name} table's columns must be exactly {list(columns)}"]
    first = next((i for i, line in enumerate(section) if line.strip().startswith("|")), None)
    if first is None:
        return [], columns_problem
    block: List[str] = []
    for line in section[first:]:
        if not line.strip():
            break
        block.append(line)
    table = [line.strip() for line in block if line.strip().startswith("|")]
    if len(table) < 2 or tuple(_cells(table[0])) != tuple(columns):
        return [], columns_problem
    stray = next((line for line in block if not line.strip().startswith("|")), None)
    if stray is not None:
        return [], [f"the {name} table has a row that is not a |-delimited table row: {stray.strip()[:60]!r}"]
    open_row = next((line for line in table if not _closed(line)), None)
    if open_row is not None:
        return [], [f"the {name} table has a row without its closing '|': {open_row[-60:]!r}"]
    outside = next((line for line in [*section[:first], *section[first + len(block) :]] if _row_like(line)), None)
    if outside is not None:
        return [], [f"a row outside the {name} table (a blank line ends a table): {outside.strip()[:60]!r}"]
    separator = _cells(table[1])
    if len(separator) != len(columns) or not all(SEPARATOR.match(cell) for cell in separator):
        return [], [f"the {name} table's second line must be its |---| separator (a row there would skip every check)"]
    return [_cells(line) for line in table[2:]], []


def _row_problems(cells: Sequence[str], seen: set, *, repo: str, today: str, latest: str) -> List[str]:
    """Every contract violation of one data row (``latest``: the newest plausible dismissal date)."""
    alert, rule, location, reason, dismissed, rationale, owner, review_by, verdict = cells
    problems: List[str] = []
    link = re.compile(
        rf"^\[#([1-9][0-9]*)\]\(https://github\.com/{re.escape(repo)}/security/code-scanning/([1-9][0-9]*)\)$"
    )
    match = link.match(alert)
    if not match or match.group(1) != match.group(2):
        problems.append(f"alert cell {alert!r} is not a link to its own alert number in {repo}")
    elif match.group(1) in seen:
        problems.append(f"alert #{match.group(1)} is logged twice")
    else:
        seen.add(match.group(1))
    if not RULE_ID.match(rule):
        problems.append(f"rule {rule!r} is not a backticked query id")
    if not LOCATION.match(location):
        problems.append(f"location {location!r} is not a backticked path:line")
    if reason not in REASONS:
        problems.append(f"reason {reason!r} is not one of {sorted(REASONS)}")
    if not _is_date(dismissed):
        problems.append(f"dismissed date {dismissed!r} is not a real YYYY-MM-DD date")
    elif dismissed > latest:
        problems.append(f"dismissed date {dismissed} is in the future")
    if rationale in MISSING:
        problems.append("rationale is empty")
    if verdict in MISSING:
        problems.append("verdict is empty")
    pending = verdict.startswith(PENDING) and _is_date(dismissed) and dismissed < POLICY_DATE
    if reason == "won't fix" and not pending:
        if owner in MISSING:
            problems.append("an accepted `won't fix` names no owner")
        if not _is_date(review_by):
            problems.append(f"an accepted `won't fix` has no real review-by date (got {review_by!r})")
    elif review_by not in MISSING and not _is_date(review_by):
        problems.append(f"review-by {review_by!r} is neither '-' nor a real date")
    if _is_date(review_by):
        if _is_date(dismissed) and review_by <= dismissed:
            problems.append(f"review-by {review_by} is not after its dismissal {dismissed}")
        elif review_by < today:
            problems.append(f"the dismissal was due for review on {review_by}: re-review it and log the outcome")
    return problems


def _today(today: Optional[dt.date]) -> dt.date:
    # timezone.utc, not datetime.UTC (3.11+): governed repos run this on 3.9 and 3.10.
    return today or dt.datetime.now(dt.timezone.utc).date()  # noqa: UP017


def _latest(day: dt.date) -> str:
    """The newest plausible record date: one day of slack, since a record logged east of UTC can carry tomorrow."""
    return (day + dt.timedelta(days=1)).isoformat()


def _log_rows(text: str) -> Tuple[List[List[str]], List[str]]:
    """The log table's rows, or the problem that stops reading it."""
    sections = _sections(text.splitlines(), LOG_HEADING)
    if not sections:
        return [], ["no '## Log' or '## Log (<repo>)' section"]
    if len(sections) > 1:
        return [], [f"{len(sections)} '## Log' sections: keep one, or the rows under the others skip every check"]
    return _table(sections[0], COLUMNS, "log")


def logged_alerts(text: str) -> List[str]:
    """The alert numbers in the VALIDATED log table only (none when it cannot be read)."""
    rows, problems = _log_rows(text)
    links = (ALERT_NUMBER.match(cells[0]) for cells in rows if not problems and cells)
    return [link.group(1) for link in links if link]


def unlogged_dismissals(text: str, dismissed: Sequence[Mapping[str, Any]]) -> List[str]:
    """Every alert GitHub reports dismissed that the log does not record, or records with another reason.

    ``dismissed``: GitHub's dismissed alerts (``number``, ``dismissed_reason``).
    A log that cannot be read reconciles nothing; ``validate`` says why.
    """
    rows, problems = _log_rows(text)
    if problems:
        return []
    logged: Dict[str, str] = {}
    for cells in rows:
        match = ALERT_NUMBER.match(cells[0]) if len(cells) == len(COLUMNS) else None
        if match:
            logged[match.group(1)] = cells[3]
    found: List[str] = []
    for alert in dismissed:
        number, reason = str(alert.get("number", "")), str(alert.get("dismissed_reason") or "")
        if number not in logged:
            found.append(f"alert #{number} is dismissed on GitHub ({reason}) but has no log row")
        elif logged[number] != reason:
            found.append(f"alert #{number} is dismissed on GitHub as {reason!r} but logged as {logged[number]!r}")
    return found


def validate(text: str, *, repo: str, today: Optional[dt.date] = None) -> List[str]:
    """Return every problem with the log in ``text`` for ``repo`` (empty when well-formed)."""
    rows, problems = _log_rows(text)
    if problems:
        return problems
    day = _today(today)
    latest = _latest(day)
    seen: set = set()
    for number, cells in enumerate(rows, start=1):
        if len(cells) != len(COLUMNS):
            problems.append(f"row {number}: {len(cells)} cells, expected {len(COLUMNS)}")
            continue
        found = _row_problems(cells, seen, repo=repo, today=day.isoformat(), latest=latest)
        problems.extend(f"row {number}: {problem}" for problem in found)
    return problems


def _decision_row_problems(cells: Sequence[str], *, latest: str) -> List[str]:
    """Every contract violation of one query configuration decision."""
    if len(cells) != len(DECISION_COLUMNS):
        return [f"{len(cells)} cells, expected {len(DECISION_COLUMNS)}"]
    query, decided, decision = cells
    problems: List[str] = []
    if not QUERY_CELL.match(query):
        problems.append(f"query {query!r} is not a comma-separated list of backticked query ids")
    match = DECIDED.match(decided)
    if not match or not _is_date(match.group(1)):
        problems.append(f"decided {decided!r} is neither a real YYYY-MM-DD date nor 'before YYYY-MM-DD'")
    elif match.group(1) > latest:
        problems.append(f"decided {decided!r} is in the future")
    if decision in MISSING:
        problems.append("decision is empty")
    return problems


def _decision_rows(text: str) -> Tuple[List[List[str]], List[str]]:
    """The decisions table's rows, or the problem that stops reading it (no section: no rows, no problem)."""
    sections = _sections(text.splitlines(), DECISIONS_HEADING)
    if len(sections) > 1:
        return [], [f"{len(sections)} '{DECISIONS_TITLE}' sections: keep one"]
    rows, problems = _table(sections[0], DECISION_COLUMNS, "decisions") if sections else ([], [])
    # One decision per query: a stale "**Excluded**" row must not outvote a later "not excluded".
    seen: Dict[str, int] = {}
    for cells in rows:
        for query in QUERY_ID.findall(cells[0]):
            seen[query] = seen.get(query, 0) + 1
    twice = sorted(query for query, count in seen.items() if count > 1)
    if twice:
        return [], [f"more than one decision for {', '.join(twice)}: keep one row per query, updated in place"]
    return rows, problems


def decision_problems(text: str, *, today: Optional[dt.date] = None) -> List[str]:
    """Every problem with the query configuration decisions table (none when there is no such section)."""
    rows, problems = _decision_rows(text)
    latest = _latest(_today(today))
    for number, cells in enumerate(rows, start=1):
        problems.extend(f"decision {number}: {problem}" for problem in _decision_row_problems(cells, latest=latest))
    return problems


def documented_queries(text: str, *, today: Optional[dt.date] = None) -> set:
    """The query ids of every well-formed decision that AUTHORIZES the exclusion (starts ``**Excluded``).

    A malformed row documents nothing, and neither does a decision NOT to
    exclude ("**Fixed in the code ..., not excluded.**").
    """
    rows, problems = _decision_rows(text)
    if problems:
        return set()
    latest = _latest(_today(today))
    return {
        query
        for cells in rows
        if not _decision_row_problems(cells, latest=latest) and cells[2].startswith(EXCLUDED)
        for query in QUERY_ID.findall(cells[0])
    }


def undocumented_exclusions(config: Any, text: str, *, today: Optional[dt.date] = None) -> List[str]:
    """Every CodeQL query filter in ``config`` that no exclusion decision in ``text`` authorizes (rule 4)."""
    documented = documented_queries(text, today=today)
    filters = config.get("query-filters") if isinstance(config, Mapping) else None
    problems: List[str] = []
    for entry in filters or []:
        exclude = entry.get("exclude") if isinstance(entry, Mapping) else None
        if not isinstance(exclude, Mapping):
            continue
        for key, value in exclude.items():
            if key != "id":
                problems.append(f"a query filter excludes by {key!r}; exclude by query id so each decision is listed")
                continue
            for query in value if isinstance(value, list) else [value]:
                if str(query) not in documented:
                    problems.append(
                        f"excluded query {query!r} has no '{EXCLUDED}...' decision under '{DECISIONS_TITLE}'"
                    )
    return problems


def is_security_finding(finding: Mapping[str, Any]) -> bool:
    """Whether rule 2's security rationale applies, whichever tool raised the finding.

    A finding is security when it carries a security severity, a ``security``,
    CWE or OWASP tag, or comes from a security-only scanner. Reads a
    code-scanning alert (GitHub API: ``rule.security_severity_level``,
    ``rule.tags``, ``tool.name``) or a SARIF rule or result
    (``properties.security-severity``, ``properties.tags``), so a Semgrep
    result tagged only ``security`` and ``CWE-95`` counts as security.
    """
    tool = finding.get("tool")
    if isinstance(tool, Mapping) and str(tool.get("name", "")).lower() in SECURITY_TOOLS:
        return True
    views = [view for view in (finding, finding.get("rule")) if isinstance(view, Mapping)]
    views += [view["properties"] for view in list(views) if isinstance(view.get("properties"), Mapping)]
    if any(view.get("security_severity_level") or view.get("security-severity") for view in views):
        return True
    return any(SECURITY_TAG.match(str(tag)) for view in views for tag in view.get("tags") or [])


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI: exit 1 and list every problem when the log or its query decisions are incomplete."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", default="docs/CODE-SCANNING-DISMISSALS.md")
    parser.add_argument("--repo", help="owner/name whose alerts the log may link (required to validate)")
    parser.add_argument(
        "--codeql-config",
        default=".github/codeql/codeql-config.yml",
        help="CodeQL config whose every query exclusion must be a listed decision (skipped when absent)",
    )
    parser.add_argument(
        "--dismissed-jsonl",
        default="",
        help="GitHub's dismissed alerts, one {number, dismissed_reason} per line: each must have a log row",
    )
    parser.add_argument("--classify", action="store_true", help="classify the code-scanning alert JSON on stdin")
    args = parser.parse_args(argv)
    if args.classify:
        try:
            alert = json.load(sys.stdin)
        except json.JSONDecodeError:
            alert = None
        # An API error body ({"message": "Not Found"}) is no alert: never classify it.
        if not isinstance(alert, Mapping) or not isinstance(alert.get("rule"), Mapping):
            parser.error("--classify reads one code-scanning alert (with its rule) as JSON on stdin")
        print("security" if is_security_finding(alert) else "correctness")
        return 0
    if not args.repo:
        parser.error("--repo is required to validate a log")
    text = Path(args.path).read_text(encoding="utf-8")
    problems = [f"{args.path}: {problem}" for problem in validate(text, repo=args.repo) + decision_problems(text)]
    if args.dismissed_jsonl:
        lines = Path(args.dismissed_jsonl).read_text(encoding="utf-8").splitlines()
        dismissed = [json.loads(line) for line in lines if line.strip()]
        problems += [f"{args.path}: {problem}" for problem in unlogged_dismissals(text, dismissed)]
    config = Path(args.codeql_config)
    if config.is_file():
        loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
        problems += [f"{config}: {problem}" for problem in undocumented_exclusions(loaded, text)]
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
