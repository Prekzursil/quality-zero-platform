#!/usr/bin/env python3
r"""Parse and round-trip ``BEGIN/END quality-zero:<region>`` marker regions.

The drift-sync workflow ships template-owned chunks of consumer files
wrapped in matched markers; anything outside the markers is user-owned
and preserved verbatim. See ``docs/QZP-V2-DESIGN.md`` §4 for the design.

Contract:

* A region starts with a line matching ``BEGIN quality-zero:<region-id>``
  (the ``-id`` suffix is arbitrary ASCII minus ``\s/<>``).
* A region ends at the next line matching ``END quality-zero:<region-id>``
  with the *same* region-id. Mismatched or unterminated regions raise
  ``MarkerRegionError`` so templates can't silently corrupt files.
* A file may contain any number of top-level regions. Nesting is NOT
  allowed — a second ``BEGIN`` before the current ``END`` is a
  ``NestedRegionError``.
* ``parse_regions`` returns the list of ``Region`` records in source
  order; ``replace_regions`` rebuilds the file verbatim except for
  regions whose id matches a provided overrides mapping.
* Lines containing the markers are preserved byte-for-byte so the
  comment prefix (``#``, ``//``, ``<!-- --`` etc.) in the consumer file
  is not clobbered by the renderer.
"""

from __future__ import absolute_import

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI invocation."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    # Only runs when invoked outside pytest (where the root is already staged).
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()


class MarkerRegionError(ValueError):
    """Base class for every structural error the parser rejects."""


class UnterminatedRegionError(MarkerRegionError):
    """``BEGIN`` seen with no matching ``END`` before EOF."""


class MismatchedRegionError(MarkerRegionError):
    """``END`` seen with a region-id that does not match the open ``BEGIN``."""


class NestedRegionError(MarkerRegionError):
    """A second ``BEGIN`` seen before the current region closed."""


@dataclass(frozen=True)
class Region:
    """One parsed marker region.

    ``body`` is the exact text between the BEGIN and END lines, including
    any trailing newline; ``begin_line`` / ``end_line`` are the 1-based
    line numbers of the marker lines themselves (so the owned content
    sits between them exclusive).
    """

    region_id: str
    body: str
    begin_line: int
    end_line: int


# The region-id allows letters, digits, dashes, dots, underscores, and colons.
# Deliberately excludes whitespace, ``/``, ``<``, ``>`` so the marker never
# collides with path segments or HTML/XML tokens the comment prefix might use.
_REGION_ID = r"[A-Za-z0-9_.:\-]+"
_BEGIN_RE = re.compile(rf"BEGIN\s+quality-zero:({_REGION_ID})")
_END_RE = re.compile(rf"END\s+quality-zero:({_REGION_ID})")


def _handle_begin(
    begin_id: str, lineno: int, open_region: Optional[Tuple[str, int, List[str]]]
) -> Tuple[str, int, List[str]]:
    """Validate and open a new region, raising if another is already open."""
    if open_region is not None:
        raise NestedRegionError(
            f"nested BEGIN on line {lineno}: region "
            f"{begin_id!r} opened while {open_region[0]!r} "
            f"is still open from line {open_region[1]}"
        )
    return (begin_id, lineno, [])


def _handle_end(
    end_id: str, lineno: int, open_region: Optional[Tuple[str, int, List[str]]]
) -> Region:
    """Validate the END marker, returning the closed ``Region``."""
    if open_region is None:
        raise MismatchedRegionError(
            f"END on line {lineno} ({end_id!r}) without a matching BEGIN"
        )
    region_id, begin_line, body_lines = open_region
    if end_id != region_id:
        raise MismatchedRegionError(
            f"END on line {lineno} has id {end_id!r}; "
            f"expected {region_id!r} (opened on line {begin_line})"
        )
    return Region(
        region_id=region_id,
        body="".join(body_lines),
        begin_line=begin_line,
        end_line=lineno,
    )


def parse_regions(text: str) -> List[Region]:
    """Return every marker region found in ``text`` in source order.

    Raises ``MarkerRegionError`` subclass for structural violations.
    The per-line branching is delegated to ``_handle_begin`` and
    ``_handle_end`` helpers so this coordinator stays under qlty's
    function-complexity ceiling.
    """
    regions: List[Region] = []
    open_region: Optional[Tuple[str, int, List[str]]] = None
    # Preserve trailing-newline behaviour: splitlines() drops the final
    # ``\n`` but we want to round-trip exactly. We track line endings
    # separately instead of re-joining later.
    for lineno, raw in enumerate(text.splitlines(keepends=True), start=1):
        begin_match = _BEGIN_RE.search(raw)
        end_match = _END_RE.search(raw)
        if begin_match and not end_match:
            open_region = _handle_begin(begin_match.group(1), lineno, open_region)
            continue
        if end_match and not begin_match:
            regions.append(_handle_end(end_match.group(1), lineno, open_region))
            open_region = None
            continue
        if open_region is not None:
            open_region[2].append(raw)
    if open_region is not None:
        raise UnterminatedRegionError(
            f"BEGIN on line {open_region[1]} "
            f"({open_region[0]!r}) has no matching END before EOF"
        )
    return regions


def _normalise_override_body(body: str) -> str:
    """Return ``body`` with a trailing newline so the END marker starts fresh."""
    if body and not body.endswith("\n"):
        return body + "\n"
    return body


def _emit_begin(
    raw: str, region_id: str, overrides: Mapping[str, str], out: List[str]
) -> None:
    """Append the BEGIN line and, when overridden, the new body."""
    out.append(raw)
    if region_id in overrides:
        out.append(_normalise_override_body(overrides[region_id]))


def _dispatch_replace_line(
    raw: str,
    open_region: Optional[str],
    overrides: Mapping[str, str],
    out: List[str],
) -> Optional[str]:
    """Route one line of the replacement loop; return the new ``open_region``.

    * BEGIN line: emit it (and the override body when applicable), and
      return the new region-id as the next iteration's ``open_region``.
    * END line: emit the END line verbatim and close the region.
    * Inside overridden region: drop the original body.
    * Anywhere else: pass the line through.
    """
    begin_match = _BEGIN_RE.search(raw)
    end_match = _END_RE.search(raw)
    if begin_match and not end_match:
        region_id = begin_match.group(1)
        _emit_begin(raw, region_id, overrides, out)
        return region_id
    if end_match and not begin_match:
        out.append(raw)
        return None
    if open_region is not None and open_region in overrides:
        # Inside an overridden region: drop original body lines; the
        # override already wrote them during ``_emit_begin``.
        return open_region
    out.append(raw)
    return open_region


def replace_regions(text: str, overrides: Mapping[str, str]) -> str:
    """Return ``text`` with each region whose id appears in ``overrides``
    swapped out for the new body. Regions not in ``overrides`` pass through
    unchanged, as does every byte outside any region (including the BEGIN/END
    lines themselves).

    Non-overridden files round-trip byte-perfect when ``overrides`` is empty.
    """
    if not overrides:
        # Round-trip guarantee: do not even tokenise the file.
        return text
    out: List[str] = []
    open_region: Optional[str] = None
    for raw in text.splitlines(keepends=True):
        open_region = _dispatch_replace_line(raw, open_region, overrides, out)
    return "".join(out)


def region_ids(text: str) -> List[str]:
    """Return just the region ids, in source order. Convenience helper."""
    return [r.region_id for r in parse_regions(text)]


def region_bodies(text: str) -> Dict[str, str]:
    """Return ``{region_id: body}`` for every region. Useful for diffing."""
    return {r.region_id: r.body for r in parse_regions(text)}


if __name__ == "__main__":  # pragma: no cover — CLI wrapper for ad-hoc checks
    if len(sys.argv) < 2:
        print("usage: marker_regions.py <file>", file=sys.stderr)
        raise SystemExit(2)
    path = Path(sys.argv[1])
    for region in parse_regions(path.read_text(encoding="utf-8")):
        print(f"{region.region_id}\t{region.begin_line}\t{region.end_line}")
