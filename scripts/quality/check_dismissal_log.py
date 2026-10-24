#!/usr/bin/env python3
"""Validate a code-scanning dismissal log (docs/CODE-SCANNING-DISMISSALS.md).

The dismissal rule makes every dismissal auditable. This check keeps the log
itself well-formed so the audit trail cannot drift silently:

* the log table has exactly the agreed columns and a real separator row;
* every row links an alert OF THIS REPOSITORY, names a backticked rule, a
  ``path:line`` location, a GitHub dismissal reason, a real calendar date, a
  rationale and a verdict, and no alert appears twice;
* a ``won't fix`` row that is not marked for re-review names an owner and a
  real review-by date (accepted risk always has someone answerable and a date).
"""

from __future__ import absolute_import

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence

LOG_HEADING = "## Log"
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
LOCATION = re.compile(r"^`[^`\s]+:[1-9][0-9]*`$")
PENDING = ("**Re-review", "**Fix")


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


def _row_problems(cells: Sequence[str], seen: set, *, repo: str) -> List[str]:
    """Every contract violation of one data row."""
    alert, rule, location, reason, dismissed, rationale, owner, review_by, verdict = cells
    problems: List[str] = []
    link = re.compile(rf"^\[#(\d+)\]\(https://github\.com/{re.escape(repo)}/security/code-scanning/(\d+)\)$")
    match = link.match(alert)
    if not match or match.group(1) != match.group(2):
        problems.append(f"alert cell {alert!r} is not a link to its own alert number in {repo}")
    elif match.group(1) in seen:
        problems.append(f"alert #{match.group(1)} is logged twice")
    else:
        seen.add(match.group(1))
    if not (rule.startswith("`") and rule.endswith("`") and len(rule) > 2):
        problems.append(f"rule {rule!r} is not a backticked query id")
    if not LOCATION.match(location):
        problems.append(f"location {location!r} is not a backticked path:line")
    if reason not in REASONS:
        problems.append(f"reason {reason!r} is not one of {sorted(REASONS)}")
    if not _is_date(dismissed):
        problems.append(f"dismissed date {dismissed!r} is not a real YYYY-MM-DD date")
    if not rationale:
        problems.append("rationale is empty")
    if not verdict:
        problems.append("verdict is empty")
    if reason == "won't fix" and not verdict.startswith(PENDING):
        if owner in ("", "-"):
            problems.append("an accepted `won't fix` names no owner")
        if not _is_date(review_by):
            problems.append(f"an accepted `won't fix` has no real review-by date (got {review_by!r})")
    return problems


def validate(text: str, *, repo: str) -> List[str]:
    """Return every problem with the log in ``text`` for ``repo`` (empty when well-formed)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(LOG_HEADING)), None)
    if start is None:
        return [f"no '{LOG_HEADING} ...' section"]
    table = [line for line in lines[start + 1 :] if line.startswith("|")]
    if len(table) < 2 or tuple(_cells(table[0])) != COLUMNS:
        return [f"the log table's columns must be exactly {list(COLUMNS)}"]
    separator = _cells(table[1])
    if len(separator) != len(COLUMNS) or not all(SEPARATOR.match(cell) for cell in separator):
        return ["the log table's second line must be its |---| separator (a row there would skip every check)"]
    problems: List[str] = []
    seen: set = set()
    for number, line in enumerate(table[2:], start=1):
        cells = _cells(line)
        if len(cells) != len(COLUMNS):
            problems.append(f"row {number}: {len(cells)} cells, expected {len(COLUMNS)}")
            continue
        problems.extend(f"row {number}: {problem}" for problem in _row_problems(cells, seen, repo=repo))
    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI: exit 1 and list every problem when the log is malformed."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", default="docs/CODE-SCANNING-DISMISSALS.md")
    parser.add_argument("--repo", required=True, help="owner/name whose alerts the log may link")
    args = parser.parse_args(argv)
    problems = validate(Path(args.path).read_text(encoding="utf-8"), repo=args.repo)
    for problem in problems:
        print(f"{args.path}: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
