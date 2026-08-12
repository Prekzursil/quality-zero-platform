#!/usr/bin/env python3
"""Gate 7 - cyclomatic complexity, scoped to NEW CODE ONLY (T1).

``lizard`` measures every function in the tree. A gate that blocked on all of
them would be unreachable on any repo with history: it would demand the owner
refactor code the change never touched, which is the treadmill the lean charter
exists to escape. So this module splits lizard's report into two tiers:

* **BLOCK (T1)** - the change added or modified lines *inside* the function's own
  span AND its CCN is over the bar. New complexity cannot be merged.
* **T2 inventory** - every other over-threshold function. PRINTED in full,
  never blocking. Legacy debt is made visible, not enforced.

The scope is LINE-level, not file-level, and that distinction is the whole
design. Touching one line of a 900-line legacy module must not summon every
knot in it. ``tests/fixtures/newcode/`` pins the case that proves it:
``src/legacy_knot.py`` IS a changed file, but the only hunk adds lines 48-52
while the CCN=21 ``classify`` occupies lines 4-42 - so ``classify`` is T2 and
the newly-added ``Knot`` (CCN=18) blocks.

**"New" is defined by the diff, never by a time window.** SonarQube Cloud's
"number of days" new-code period lets an unfixed finding age into legacy on day
31, so the gate goes green with the defect still in the code - a decay function
wearing a gate's clothes. The only accepted scope input here is a unified diff.

Threshold. The default is **CCN 15**: it is lizard's own ``-C`` default, so a
developer running ``lizard`` locally sees the same verdict CI does, and it is
already the value in the estate's tool-routing table (``lizard -C 15``).
McCabe's 1976 paper proposed 10; NIST 500-235 endorses 10 while explicitly
allowing a limit up to 15 for well-tested code. 15 is therefore the lenient end
of the defensible range - deliberately so, because this gate BLOCKS, and a
blocking bar should be one nobody can argue is arbitrary. Tighten per-repo via
``--max-ccn`` if desired; it is rejected outside 1..100 so neither a
block-everything 0 nor a switched-off 10000 is expressible.

When no diff is supplied (a push to ``main``, a merge group - there is no base to
diff against) the gate emits the T2 inventory and **passes**. It never blocks
without a scope.

Exit codes: ``0`` nothing new is over the bar, ``1`` new code is over the bar,
``2`` the input or the threshold itself is unusable (never a silent pass).
"""

from __future__ import absolute_import

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_MAX_CCN = 15
MIN_ACCEPTED_CCN = 1
MAX_ACCEPTED_CCN = 100

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_CONFIG_ERROR = 2

#: ``lizard --csv`` emits no header. Column order, verified against recorded output:
#: NLOC, CCN, token_count, param_count, length, name@start-end@file, file,
#: function, signature, start, end
LIZARD_COLUMNS = 11
_CCN_INDEX = 1
_FILE_INDEX = 6
_NAME_INDEX = 7
_START_INDEX = 9
_END_INDEX = 10


class ThresholdError(ValueError):
    """The ``--max-ccn`` argument cannot be read as a usable bar."""


@dataclass(frozen=True)
class FunctionMetric:
    """One function, as lizard measured it."""

    path: str
    name: str
    ccn: int
    start: int
    end: int


@dataclass(frozen=True)
class LizardReport:
    """A parsed lizard report, plus how much of it could not be read."""

    functions: List[FunctionMetric]
    skipped_rows: int


@dataclass(frozen=True)
class Verdict:
    """The tiering decision for one report."""

    blocking: List[FunctionMetric]
    inventory: List[FunctionMetric]
    scoped: bool


# jscpd:ignore-start
# ─── SHARED NEW-CODE SCOPING ────────────────────────────────────────────────
# This block is BYTE-IDENTICAL in complexity_gate.py and duplication_gate.py,
# and tests/test_newcode_scope_parity.py fails if the two ever diverge.
#
# Why it is duplicated rather than imported: both modules are embedded verbatim
# into .github/workflows/reusable-quality.yml and run as standalone scripts in a
# CALLER's checkout, which has no `scripts/` tree. Neither can import the other,
# and neither can import a third shared module. Duplication is only safe when
# drift is impossible - the same argument test_lean_gate_embedded_helpers.py
# already makes for the embedded workflow copies.
#
# The jscpd markers suppress gate 8 on this one block. They are INLINE and
# therefore visible in review, deliberately not a .jscpd.json exclusion: a
# config-file suppression is invisible in a diff, and a suppressed finding has
# to stay countable (see the qlty `[[triage]]` trap - suppress-at-generation
# makes a dirty dashboard pixel-identical to a clean one).

#: file -> the line ranges this change added or modified, 1-based and inclusive.
AddedRanges = Dict[str, List[Tuple[int, int]]]

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def normalize_path(raw: str, root: Optional[str] = None) -> str:
    """Collapse any recorded path convention to one repo-relative POSIX form.

    The reporting tools disagree with each other and with git. lizard records
    ``.\\src\\x.py`` on Windows and ``./src/x.py`` on Linux; jscpd records
    ``src\\x.py``, or an absolute path under ``--absolute``. The diff always
    speaks repo-relative POSIX, so every path must be reduced to that or the
    per-file lookup silently misses and the gate fails OPEN.
    """
    text = raw.strip().strip('"').replace("\\", "/")
    if root:
        prefix = root.replace("\\", "/").rstrip("/") + "/"
        if text.startswith(prefix):
            text = text[len(prefix) :]
    while text.startswith("./"):
        text = text[2:]
    return text


def _header_target(line: str) -> Optional[str]:
    """Return the new-side path of a ``+++`` header, or ``None`` for a deletion."""
    target = line[4:].split("\t")[0].strip()
    if target == "/dev/null":
        return None
    if target.startswith("b/"):
        target = target[2:]
    return normalize_path(target)


def parse_added_ranges(diff_text: str) -> AddedRanges:
    """Extract the added/modified line ranges of a unified diff, per file.

    Only the NEW side matters: a range is what the change put in the tree. A
    pure-deletion hunk (``+40,0``) contributes nothing, and a deleted file
    (``+++ /dev/null``) has no new side at all.

    A ``+++`` line only counts as a file header when it directly follows a
    ``---`` one. Adding a source line whose own text begins ``++ `` renders as
    ``+++ ...`` inside a hunk body, and treating that as a header would
    silently retarget every range that followed it.
    """
    ranges: AddedRanges = {}
    current: Optional[str] = None
    previous_was_old_header = False
    for line in diff_text.splitlines():
        if line.startswith("--- "):
            previous_was_old_header = True
            continue
        if previous_was_old_header and line.startswith("+++ "):
            previous_was_old_header = False
            current = _header_target(line)
            continue
        previous_was_old_header = False
        if not line.startswith("@@"):
            continue
        match = _HUNK.match(line)
        if match is None or current is None:
            continue
        start = int(match.group(1))
        count = 1 if match.group(2) is None else int(match.group(2))
        if count <= 0:
            continue
        ranges.setdefault(current, []).append((start, start + count - 1))
    return ranges


def spans_overlap(path: str, start: int, end: int, added_ranges: AddedRanges) -> bool:
    """Return whether the change added or modified a line inside ``[start, end]``.

    This is what makes the scope LINE-level rather than file-level. Touching one
    line of a legacy module must not summon every pre-existing finding in it.
    """
    return any(start <= high and low <= end for low, high in added_ranges.get(path, ()))


class ScopeInputError(ValueError):
    """The unified diff that defines new code cannot be read."""


def load_added_ranges(diff_path: Optional[str]) -> AddedRanges:
    """Read the added ranges of ``diff_path``, or an empty scope when none was given.

    No diff means no base to diff against (a push to the default branch, a merge
    group), which is a legitimate unscoped run. But a caller that PROMISED a diff
    and cannot supply a readable one is an ERROR, never "no scope": silently
    degrading to unscoped would turn a broken invocation into a permanent pass.
    """
    if diff_path is None:
        return {}
    try:
        return parse_added_ranges(Path(diff_path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ScopeInputError(
            f"cannot read the diff {diff_path!r} ({exc}). The caller promised a new-code scope, "
            "so an unreadable diff is an error, not 'no scope'."
        ) from None


# jscpd:ignore-end


def parse_lizard_csv(text: str, root: Optional[str] = None) -> LizardReport:
    """Parse ``lizard --csv`` output.

    Unreadable rows are COUNTED rather than dropped: a partially-parsed report
    that reads as clean is the exact shape of a silent pass, so the count is
    surfaced in the rendered output.
    """
    functions: List[FunctionMetric] = []
    skipped = 0
    for row in csv.reader(text.splitlines()):
        if not row:
            continue
        if len(row) < LIZARD_COLUMNS:
            skipped += 1
            continue
        try:
            ccn = int(row[_CCN_INDEX])
            start = int(row[_START_INDEX])
            end = int(row[_END_INDEX])
        except ValueError:
            skipped += 1
            continue
        functions.append(
            FunctionMetric(
                path=normalize_path(row[_FILE_INDEX], root),
                name=row[_NAME_INDEX],
                ccn=ccn,
                start=start,
                end=end,
            )
        )
    return LizardReport(functions=functions, skipped_rows=skipped)


def classify(
    functions: Sequence[FunctionMetric],
    added_ranges: AddedRanges,
    max_ccn: int,
    scoped: bool,
) -> Verdict:
    """Split the over-threshold functions into the blocking set and the T2 set."""
    blocking: List[FunctionMetric] = []
    inventory: List[FunctionMetric] = []
    for function in functions:
        if function.ccn <= max_ccn:
            continue
        if scoped and spans_overlap(function.path, function.start, function.end, added_ranges):
            blocking.append(function)
        else:
            inventory.append(function)
    return Verdict(blocking=blocking, inventory=inventory, scoped=scoped)


def _row(function: FunctionMetric) -> str:
    """Render one function as an actionable line: where, what, how bad."""
    return f"  {function.path}:{function.start}-{function.end}  {function.name}  CCN={function.ccn}"


def render_report(report: LizardReport, verdict: Verdict, max_ccn: int) -> str:
    """Render the full verdict, T2 included.

    The demoted set is printed unconditionally. A gate that hides what it chose
    not to enforce is indistinguishable from one that found nothing.
    """
    over = len(verdict.blocking) + len(verdict.inventory)
    lines = [
        f"complexity: {len(report.functions)} function(s) measured, {over} over the bar (lizard CCN <= {max_ccn})."
    ]
    if report.skipped_rows:
        lines.append(
            f"WARNING gate-complexity: {report.skipped_rows} unreadable row(s) in the lizard report were "
            "not measured. The report is incomplete, so treat this run as partial."
        )
    if verdict.scoped:
        lines.append("scope: lines this change added or modified (T1 new-code-only).")
    else:
        lines.append("scope: whole repo, no diff base - inventory only, nothing can block.")
    if verdict.blocking:
        lines.append(f"BLOCKING ({len(verdict.blocking)}) - new or changed lines inside an over-the-bar function:")
        lines.extend(_row(function) for function in verdict.blocking)
    if verdict.inventory:
        lines.append(
            f"T2 INVENTORY ({len(verdict.inventory)}) - pre-existing complexity on lines this change did not "
            "touch; visible and tracked, deliberately NOT blocking:"
        )
        lines.extend(_row(function) for function in verdict.inventory)
    return "\n".join(lines)


def parse_max_ccn(raw: str) -> int:
    """Validate the CCN bar, rejecting anything that would neuter the gate."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ThresholdError(f"--max-ccn must be a whole number, got {raw!r}") from None
    if value < MIN_ACCEPTED_CCN or value > MAX_ACCEPTED_CCN:
        raise ThresholdError(
            f"--max-ccn must be between {MIN_ACCEPTED_CCN} and {MAX_ACCEPTED_CCN}, got {value}. "
            "A bar below 1 blocks every function; one above the ceiling switches the gate off."
        )
    return value


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI."""
    parser = argparse.ArgumentParser(description="Gate 7: block NEW code whose cyclomatic complexity is over the bar.")
    parser.add_argument("--csv", required=True, help="path to a `lizard --csv` report")
    parser.add_argument(
        "--diff",
        default=None,
        help="path to the unified diff defining new code; omit to emit T2 inventory and pass",
    )
    parser.add_argument("--max-ccn", default=str(DEFAULT_MAX_CCN), help=f"CCN bar (default {DEFAULT_MAX_CCN})")
    parser.add_argument("--root", default=None, help="repo root, to relativize absolute paths in the report")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the gate; see the module docstring for the exit-code contract."""
    args = _build_parser().parse_args(argv)
    try:
        max_ccn = parse_max_ccn(args.max_ccn)
    except ThresholdError as exc:
        print(f"ERROR gate-complexity: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        csv_text = Path(args.csv).read_text(encoding="utf-8")
    except OSError as exc:
        print(
            f"ERROR gate-complexity: cannot read the lizard report {args.csv!r} ({exc}). "
            "An unmeasured tree is not a clean tree, so this is an error rather than a pass.",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR
    try:
        added_ranges = load_added_ranges(args.diff)
    except ScopeInputError as exc:
        print(f"ERROR gate-complexity: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    report = parse_lizard_csv(csv_text, args.root)
    verdict = classify(report.functions, added_ranges, max_ccn, scoped)
    print(render_report(report, verdict, max_ccn))
    if verdict.blocking:
        print(
            f"FAIL gate-complexity: {len(verdict.blocking)} function(s) this change wrote or edited exceed "
            f"CCN {max_ccn}. Split them, or raise the bar deliberately in the workflow.",
            file=sys.stderr,
        )
        return EXIT_BLOCKED
    print(
        f"PASS gate-complexity: 0 new function(s) over CCN {max_ccn}; "
        f"{len(verdict.inventory)} demoted to T2 inventory (listed above)."
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
