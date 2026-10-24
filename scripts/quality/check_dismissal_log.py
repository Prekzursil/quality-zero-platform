#!/usr/bin/env python3
"""Validate a code-scanning dismissal log (docs/CODE-SCANNING-DISMISSALS.md).

The dismissal rule makes every dismissal auditable. This check keeps the log
itself well-formed so the audit trail cannot drift silently:

* the log section holds one unbroken table with exactly the agreed columns
  and a real separator row;
* every row links an alert OF THIS REPOSITORY, names a backticked rule, a
  ``path:line`` location, a GitHub dismissal reason, a real calendar date that
  is not in the future, a rationale and a verdict, and no alert appears twice;
* a ``won't fix`` row names an owner and a real review-by date (accepted risk
  always has someone answerable and a date), unless it predates the rule and
  is marked for re-review;
* a review-by date, on any row, is after the dismissal and has not passed:
  a dismissal whose justification can expire fails until it is re-reviewed;
* every query the CodeQL configuration excludes has a row in the "Query
  configuration decisions" table.

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
from typing import Any, List, Mapping, Optional, Sequence

import yaml

#: ``## Log`` or ``## Log (<repo>)`` exactly; never ``## Logging guidance``.
LOG_HEADING = re.compile(r"^## Log(?: \(.+\))?\s*$")
DECISIONS_HEADING = re.compile(r"^## Query configuration decisions\s*$")
DECISIONS_TITLE = "## Query configuration decisions"
#: A section runs to the next h1/h2 heading (or the end of the file).
SECTION_END = re.compile(r"^#{1,2} ")
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
#: One backtick pair around a non-blank id with no padding inside it.
RULE_ID = re.compile(r"^`[^`\s](?:[^`]*[^`\s])?`$")
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


def _cells(line: str) -> List[str]:
    """Split one markdown table row into stripped cells (escaped pipes stay inside a cell)."""
    body = line.strip()[1:-1]
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


def _section(lines: Sequence[str], heading: re.Pattern[str]) -> Optional[List[str]]:
    """The lines under the first ``heading``, up to the next h1/h2 heading (None when absent)."""
    start = next((i for i, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        return None
    section: List[str] = []
    for line in lines[start + 1 :]:
        if SECTION_END.match(line):
            break
        section.append(line)
    return section


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
    if not rationale:
        problems.append("rationale is empty")
    if not verdict:
        problems.append("verdict is empty")
    pending = verdict.startswith(PENDING) and _is_date(dismissed) and dismissed < POLICY_DATE
    if reason == "won't fix" and not pending:
        if owner in ("", "-"):
            problems.append("an accepted `won't fix` names no owner")
        if not _is_date(review_by):
            problems.append(f"an accepted `won't fix` has no real review-by date (got {review_by!r})")
    elif review_by not in ("", "-") and not _is_date(review_by):
        problems.append(f"review-by {review_by!r} is neither '-' nor a real date")
    if _is_date(review_by):
        if _is_date(dismissed) and review_by <= dismissed:
            problems.append(f"review-by {review_by} is not after its dismissal {dismissed}")
        elif review_by < today:
            problems.append(f"the dismissal was due for review on {review_by}: re-review it and log the outcome")
    return problems


def validate(text: str, *, repo: str, today: Optional[dt.date] = None) -> List[str]:
    """Return every problem with the log in ``text`` for ``repo`` (empty when well-formed)."""
    section = _section(text.splitlines(), LOG_HEADING)
    if section is None:
        return ["no '## Log' or '## Log (<repo>)' section"]
    first = next((i for i, line in enumerate(section) if line.strip().startswith("|")), None)
    if first is None:
        return [f"the log table's columns must be exactly {list(COLUMNS)}"]
    # The table runs from its header to the first blank line, as Markdown
    # renders it. A row that lost its leading pipe, or that a blank line cut
    # off from the table, is reported - never silently skipped past the checks.
    block = []
    for line in section[first:]:
        if not line.strip():
            break
        block.append(line)
    table = [line.strip() for line in block if line.strip().startswith("|")]
    if len(table) < 2 or tuple(_cells(table[0])) != COLUMNS:
        return [f"the log table's columns must be exactly {list(COLUMNS)}"]
    stray = next((line for line in block if not line.strip().startswith("|")), None)
    if stray is not None:
        return [f"the log table has a row that is not a |-delimited table row: {stray.strip()[:60]!r}"]
    cut_off = next((line for line in section[first + len(block) :] if line.strip().startswith("|")), None)
    if cut_off is not None:
        return [f"a |-row after the log table's end (a blank line ends the table): {cut_off.strip()[:60]!r}"]
    separator = _cells(table[1])
    if len(separator) != len(COLUMNS) or not all(SEPARATOR.match(cell) for cell in separator):
        return ["the log table's second line must be its |---| separator (a row there would skip every check)"]
    day = today or dt.datetime.now(dt.UTC).date()
    # One day of slack: a dismissal logged east of UTC can carry tomorrow's UTC date.
    latest = (day + dt.timedelta(days=1)).isoformat()
    problems: List[str] = []
    seen: set = set()
    for number, line in enumerate(table[2:], start=1):
        cells = _cells(line)
        if len(cells) != len(COLUMNS):
            problems.append(f"row {number}: {len(cells)} cells, expected {len(COLUMNS)}")
            continue
        found = _row_problems(cells, seen, repo=repo, today=day.isoformat(), latest=latest)
        problems.extend(f"row {number}: {problem}" for problem in found)
    return problems


def documented_queries(text: str) -> set:
    """Every backticked query id in the first column of the decisions table."""
    section = _section(text.splitlines(), DECISIONS_HEADING) or []
    rows = [line for line in section if line.strip().startswith("|")]
    return {query for line in rows for query in re.findall(r"`([^`]+)`", _cells(line)[0])}


def undocumented_exclusions(config: Any, text: str) -> List[str]:
    """Every CodeQL query filter in ``config`` that the decisions table in ``text`` does not list (rule 4)."""
    documented = documented_queries(text)
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
                    problems.append(f"excluded query {query!r} has no row under '{DECISIONS_TITLE}'")
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
    parser.add_argument("--classify", action="store_true", help="classify the code-scanning alert JSON on stdin")
    args = parser.parse_args(argv)
    if args.classify:
        try:
            alert = json.load(sys.stdin)
        except json.JSONDecodeError:
            alert = None
        if not isinstance(alert, Mapping):
            parser.error("--classify reads one code-scanning alert as JSON on stdin")
        print("security" if is_security_finding(alert) else "correctness")
        return 0
    if not args.repo:
        parser.error("--repo is required to validate a log")
    text = Path(args.path).read_text(encoding="utf-8")
    problems = [f"{args.path}: {problem}" for problem in validate(text, repo=args.repo)]
    config = Path(args.codeql_config)
    if config.is_file():
        loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
        problems += [f"{config}: {problem}" for problem in undocumented_exclusions(loaded, text)]
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
