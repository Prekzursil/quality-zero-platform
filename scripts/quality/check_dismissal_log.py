#!/usr/bin/env python3
"""Validate a code-scanning dismissal log (docs/CODE-SCANNING-DISMISSALS.md).

The dismissal rule makes every dismissal auditable. This check keeps the log
itself well-formed so the audit trail cannot drift silently:

* the log table has exactly the agreed columns;
* every row names an alert link, a backticked rule, a GitHub dismissal
  reason, an ISO date, a rationale and a verdict, and no alert appears twice;
* a ``won't fix`` row that is not marked for re-review names an owner and a
  review-by date (accepted risk always has someone answerable and a date).
"""

from __future__ import absolute_import

import argparse
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
ALERT = re.compile(r"^\[#(\d+)\]\(https://github\.com/[\w.-]+/[\w.-]+/security/code-scanning/(\d+)\)$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PENDING = ("**Re-review", "**Fix")


def _cells(line: str) -> List[str]:
    """Split one markdown table row into stripped cells (escaped pipes stay inside a cell)."""
    body = line.strip()[1:-1]
    return [cell.replace("\\|", "|").strip() for cell in re.split(r"(?<!\\)\|", body)]


def _row_problems(cells: Sequence[str], seen: set) -> List[str]:
    """Every contract violation of one data row."""
    alert, rule, _location, reason, dismissed, rationale, owner, review_by, verdict = cells
    problems: List[str] = []
    match = ALERT.match(alert)
    if not match or match.group(1) != match.group(2):
        problems.append(f"alert cell {alert!r} is not a link to its own alert number")
    elif match.group(1) in seen:
        problems.append(f"alert #{match.group(1)} is logged twice")
    else:
        seen.add(match.group(1))
    if not (rule.startswith("`") and rule.endswith("`") and len(rule) > 2):
        problems.append(f"rule {rule!r} is not a backticked query id")
    if reason not in REASONS:
        problems.append(f"reason {reason!r} is not one of {sorted(REASONS)}")
    if not ISO_DATE.match(dismissed):
        problems.append(f"dismissed date {dismissed!r} is not YYYY-MM-DD")
    if not rationale:
        problems.append("rationale is empty")
    if not verdict:
        problems.append("verdict is empty")
    if reason == "won't fix" and not verdict.startswith(PENDING):
        if owner in ("", "-"):
            problems.append("an accepted `won't fix` names no owner")
        if not ISO_DATE.match(review_by):
            problems.append(f"an accepted `won't fix` has no review-by date (got {review_by!r})")
    return problems


def validate(text: str) -> List[str]:
    """Return every problem with the log in ``text`` (empty when it is well-formed)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(LOG_HEADING)), None)
    if start is None:
        return [f"no '{LOG_HEADING} ...' section"]
    table = [line for line in lines[start + 1 :] if line.startswith("|")]
    if len(table) < 2 or tuple(_cells(table[0])) != COLUMNS:
        return [f"the log table's columns must be exactly {list(COLUMNS)}"]
    problems: List[str] = []
    seen: set = set()
    for number, line in enumerate(table[2:], start=1):
        cells = _cells(line)
        if len(cells) != len(COLUMNS):
            problems.append(f"row {number}: {len(cells)} cells, expected {len(COLUMNS)}")
            continue
        problems.extend(f"row {number}: {problem}" for problem in _row_problems(cells, seen))
    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI: exit 1 and list every problem when the log is malformed."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", default="docs/CODE-SCANNING-DISMISSALS.md")
    args = parser.parse_args(argv)
    problems = validate(Path(args.path).read_text(encoding="utf-8"))
    for problem in problems:
        print(f"{args.path}: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
