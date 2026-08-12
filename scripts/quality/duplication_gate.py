#!/usr/bin/env python3
"""Gate 8 - copy/paste duplication, scoped to NEW CODE ONLY (T1).

``jscpd`` reports every clone pair in the tree. Blocking on all of them would be
unreachable on any repo with history - it would demand the owner de-duplicate
code the change never touched, which is the treadmill the lean charter exists to
escape. So this module splits jscpd's report into two tiers:

* **BLOCK (T1)** - at least one side of the pair sits on lines this change added
  or modified. The change introduced a copy, and that cannot be merged.
* **T2 inventory** - clone pairs entirely on untouched lines. PRINTED in full,
  never blocking. Legacy duplication is made visible, not enforced.

**"At least one side" is the rule, not "both sides".** The recorded fixture is
exactly why: the pair is ``src/legacy_settings.py`` (untouched) against
``src/new_settings.py`` (a new file that copy-pasted it). Requiring both sides to
be new would wave through every copy-paste-from-legacy - the single most common
way duplication actually enters a codebase - while only catching the rarer case
of someone pasting a block twice in one change.

**"New" is defined by the diff, never by a time window.** A day-count new-code
period lets an unfixed finding age into legacy and the gate goes green with the
duplication still in the tree - a decay function wearing a gate's clothes. The
only accepted scope input here is a unified diff.

Detection thresholds are jscpd's own (``--min-lines`` / ``--min-tokens``, pinned
in the workflow), deliberately NOT re-implemented here. A second filter in this
module would silently disagree with what a developer sees running jscpd locally,
and two bars for one gate is one bar too many.

When no diff is supplied (a push to ``main``, a merge group - there is no base to
diff against) the gate emits the T2 inventory and **passes**. It never blocks
without a scope.

Exit codes: ``0`` no new duplication, ``1`` new duplication, ``2`` the report is
unusable (never a silent pass).
"""

from __future__ import absolute_import

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_CONFIG_ERROR = 2

DEFAULT_LANGUAGE = "unknown"


class ReportError(ValueError):
    """The jscpd report cannot be read as a report."""


@dataclass(frozen=True)
class ClonePart:
    """One side of a clone pair: where the duplicated block sits."""

    path: str
    start: int
    end: int


@dataclass(frozen=True)
class Clone:
    """A clone pair, with the size figures jscpd measured for it."""

    first: ClonePart
    second: ClonePart
    lines: int
    tokens: int
    language: str


@dataclass(frozen=True)
class JscpdReport:
    """A parsed jscpd report, plus how much of it could not be read."""

    clones: List[Clone]
    skipped_entries: int
    reported_clones: Optional[int]


@dataclass(frozen=True)
class Verdict:
    """The tiering decision for one report."""

    blocking: List[Clone]
    inventory: List[Clone]
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


# jscpd:ignore-end


def _optional_int(value: Any, default: int = 0) -> int:
    """Read a report-only integer, falling back when it is absent or junk.

    ``lines``/``tokens`` only feed the printed row, so a junk value must not
    discard an otherwise locatable clone.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clone_part(raw: Any, root: Optional[str]) -> Optional[ClonePart]:
    """Read one side of a pair, or ``None`` when it cannot be located.

    The path and the span are load-bearing for scoping - they are what decides
    block-vs-demote - so unlike the size figures they are never defaulted.
    """
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str):
        return None
    try:
        start = int(raw["start"])
        end = int(raw["end"])
    except (KeyError, TypeError, ValueError):
        return None
    return ClonePart(path=normalize_path(name, root), start=start, end=end)


def _reported_clones(document: Dict[str, Any]) -> Optional[int]:
    """Read jscpd's own clone count, for an independent cross-check.

    ``None`` means "no cross-check available", which is not the same as a failed
    one and must not be reported as a mismatch.
    """
    statistics = document.get("statistics")
    if not isinstance(statistics, dict):
        return None
    total = statistics.get("total")
    if not isinstance(total, dict):
        return None
    value = total.get("clones")
    if not isinstance(value, int):
        return None
    return value


def parse_jscpd_json(text: str, root: Optional[str] = None) -> JscpdReport:
    """Parse ``jscpd --reporters json`` output.

    Unreadable entries are COUNTED rather than dropped: a partially-parsed report
    that reads as clean is the exact shape of a silent pass, so the count is
    surfaced in the rendered output and cross-checked against jscpd's own total.
    """
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise ReportError(f"the report is not valid JSON ({exc})") from None
    if not isinstance(document, dict):
        raise ReportError("the report is not a JSON object")
    duplicates = document.get("duplicates")
    if not isinstance(duplicates, list):
        raise ReportError(
            "the report has no `duplicates` list. A report that measured nothing is not a report of nothing."
        )
    clones: List[Clone] = []
    skipped = 0
    for entry in duplicates:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        first = _clone_part(entry.get("firstFile"), root)
        second = _clone_part(entry.get("secondFile"), root)
        if first is None or second is None:
            skipped += 1
            continue
        language = entry.get("format")
        clones.append(
            Clone(
                first=first,
                second=second,
                lines=_optional_int(entry.get("lines")),
                tokens=_optional_int(entry.get("tokens")),
                language=language if isinstance(language, str) else DEFAULT_LANGUAGE,
            )
        )
    return JscpdReport(clones=clones, skipped_entries=skipped, reported_clones=_reported_clones(document))


def classify(clones: Sequence[Clone], added_ranges: AddedRanges, scoped: bool) -> Verdict:
    """Split the clone pairs into the blocking set and the T2 set."""
    blocking: List[Clone] = []
    inventory: List[Clone] = []
    for clone in clones:
        introduced = scoped and (
            spans_overlap(clone.first.path, clone.first.start, clone.first.end, added_ranges)
            or spans_overlap(clone.second.path, clone.second.start, clone.second.end, added_ranges)
        )
        if introduced:
            blocking.append(clone)
        else:
            inventory.append(clone)
    return Verdict(blocking=blocking, inventory=inventory, scoped=scoped)


def _row(clone: Clone) -> str:
    """Render one clone pair as an actionable line: both copies, and how big."""
    return (
        f"  {clone.first.path}:{clone.first.start}-{clone.first.end}"
        f"  <->  {clone.second.path}:{clone.second.start}-{clone.second.end}"
        f"  ({clone.lines} line(s), {clone.tokens} token(s), {clone.language})"
    )


def render_report(report: JscpdReport, verdict: Verdict) -> str:
    """Render the full verdict, T2 included.

    The demoted set is printed unconditionally. A gate that hides what it chose
    not to enforce is indistinguishable from one that found nothing.
    """
    lines = [f"duplication: {len(report.clones)} clone pair(s) measured."]
    if report.skipped_entries:
        lines.append(
            f"WARNING gate-duplication: {report.skipped_entries} unreadable entry(ies) in the jscpd report could "
            "not be located. The report is incomplete, so treat this run as partial."
        )
    if report.reported_clones is not None and report.reported_clones != len(report.clones) + report.skipped_entries:
        lines.append(
            f"WARNING gate-duplication: jscpd reported {report.reported_clones} clone pair(s) in its own statistics "
            f"but the report holds {len(report.clones) + report.skipped_entries}. Two signals disagree, so this "
            "report is not trustworthy evidence of a clean tree."
        )
    if verdict.scoped:
        lines.append("scope: lines this change added or modified (T1 new-code-only).")
    else:
        lines.append("scope: whole repo, no diff base - inventory only, nothing can block.")
    if verdict.blocking:
        lines.append(f"BLOCKING ({len(verdict.blocking)}) - this change put one side of these pairs in the tree:")
        lines.extend(_row(clone) for clone in verdict.blocking)
    if verdict.inventory:
        lines.append(
            f"T2 INVENTORY ({len(verdict.inventory)}) - pre-existing duplication on lines this change did not "
            "touch; visible and tracked, deliberately NOT blocking:"
        )
        lines.extend(_row(clone) for clone in verdict.inventory)
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI."""
    parser = argparse.ArgumentParser(description="Gate 8: block duplication this change introduced.")
    parser.add_argument("--json", required=True, help="path to a `jscpd --reporters json` report")
    parser.add_argument(
        "--diff",
        default=None,
        help="path to the unified diff defining new code; omit to emit T2 inventory and pass",
    )
    parser.add_argument("--root", default=None, help="repo root, to relativize absolute paths in the report")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the gate; see the module docstring for the exit-code contract."""
    args = _build_parser().parse_args(argv)
    try:
        report_text = Path(args.json).read_text(encoding="utf-8")
    except OSError as exc:
        print(
            f"ERROR gate-duplication: cannot read the jscpd report {args.json!r} ({exc}). "
            "An unmeasured tree is not a clean tree, so this is an error rather than a pass.",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR
    added_ranges: AddedRanges = {}
    scoped = args.diff is not None
    if scoped:
        try:
            diff_text = Path(args.diff).read_text(encoding="utf-8")
        except OSError as exc:
            print(
                f"ERROR gate-duplication: cannot read the diff {args.diff!r} ({exc}). "
                "The caller promised a new-code scope, so an unreadable diff is an error, not 'no scope'.",
                file=sys.stderr,
            )
            return EXIT_CONFIG_ERROR
        added_ranges = parse_added_ranges(diff_text)
    try:
        report = parse_jscpd_json(report_text, args.root)
    except ReportError as exc:
        print(f"ERROR gate-duplication: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    verdict = classify(report.clones, added_ranges, scoped)
    print(render_report(report, verdict))
    if verdict.blocking:
        print(
            f"FAIL gate-duplication: {len(verdict.blocking)} clone pair(s) have a side this change wrote. "
            "Extract the shared block, or mark it with an inline jscpd:ignore-start/end and say why.",
            file=sys.stderr,
        )
        return EXIT_BLOCKED
    print(
        f"PASS gate-duplication: 0 new clone pair(s); {len(verdict.inventory)} demoted to T2 inventory (listed above)."
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
