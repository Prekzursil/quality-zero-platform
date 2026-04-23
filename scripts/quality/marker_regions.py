#!/usr/bin/env python3
"""Parse and round-trip ``BEGIN/END quality-zero:<region>`` marker regions.

The drift-sync workflow ships template-owned chunks of consumer files
wrapped in matched markers; anything outside the markers is user-owned
and preserved verbatim. See ``docs/QZP-V2-DESIGN.md`` §4 for the design.

Contract:

* A region starts with a line matching ``BEGIN quality-zero:<region-id>``
  (the ``-id`` suffix is arbitrary ASCII minus ``\\s/<>``).
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


def parse_regions(text: str) -> List[Region]:
    """Return every marker region found in ``text`` in source order.

    Raises ``MarkerRegionError`` subclass for structural violations.
    """
    regions: List[Region] = []
    open_region: Optional[Tuple[str, int, List[str]]] = None
    # Preserve trailing-newline behaviour: splitlines() drops the final
    # ``\n`` but we want to round-trip exactly. We track line endings
    # separately instead of re-joining later.
    lines = text.splitlines(keepends=True)
    for lineno, raw in enumerate(lines, start=1):
        begin_match = _BEGIN_RE.search(raw)
        end_match = _END_RE.search(raw)
        if begin_match and not end_match:
            if open_region is not None:
                raise NestedRegionError(
                    f"nested BEGIN on line {lineno}: region "
                    f"{begin_match.group(1)!r} opened while {open_region[0]!r} "
                    f"is still open from line {open_region[1]}"
                )
            open_region = (begin_match.group(1), lineno, [])
            continue
        if end_match and not begin_match:
            if open_region is None:
                raise MismatchedRegionError(
                    f"END on line {lineno} ({end_match.group(1)!r}) "
                    "without a matching BEGIN"
                )
            region_id, begin_line, body_lines = open_region
            if end_match.group(1) != region_id:
                raise MismatchedRegionError(
                    f"END on line {lineno} has id {end_match.group(1)!r}; "
                    f"expected {region_id!r} (opened on line {begin_line})"
                )
            regions.append(
                Region(
                    region_id=region_id,
                    body="".join(body_lines),
                    begin_line=begin_line,
                    end_line=lineno,
                )
            )
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

    lines = text.splitlines(keepends=True)
    out: List[str] = []
    open_region: Optional[str] = None
    for raw in lines:
        begin_match = _BEGIN_RE.search(raw)
        end_match = _END_RE.search(raw)
        if begin_match and not end_match:
            open_region = begin_match.group(1)
            out.append(raw)
            if open_region in overrides:
                body = overrides[open_region]
                # Preserve the trailing newline convention: if the template
                # body doesn't end in ``\n``, add one so the END marker
                # starts on its own line.
                if body and not body.endswith("\n"):
                    body = body + "\n"
                out.append(body)
            continue
        if end_match and not begin_match:
            if open_region is not None and open_region in overrides:
                # The override already emitted the body; skip the original
                # in-between content and only emit the END line.
                out.append(raw)
                open_region = None
                continue
            open_region = None
            out.append(raw)
            continue
        if open_region is not None and open_region in overrides:
            # Inside an overridden region — drop original body lines; the
            # override already wrote them.
            continue
        out.append(raw)
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
