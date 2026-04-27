"""Shared coverage dataclasses — extracted to break a cyclic import.

``assert_coverage_100`` originally hosted ``CoverageStats``, but
``coverage_parsers`` + ``coverage_findings`` need it at runtime, and they
in turn are imported (transitively) by ``coverage_support`` which
``assert_coverage_100`` imports — closing a cycle that CodeQL's
``py/unsafe-cyclic-import`` rule correctly flagged.

Moving the dataclass to a leaf module lets every consumer import from
here without dragging in the rest of the coverage stack.
"""
from __future__ import absolute_import

from dataclasses import dataclass


@dataclass
class CoverageStats:
    """Summarize line and branch coverage for one named report input."""

    name: str
    path: str
    covered: int
    total: int
    branch_covered: int = 0
    branch_total: int = 0

    @property
    def percent(self) -> float:
        """Return line coverage as a 0-100 percentage; 100 on empty input."""
        if self.total <= 0:
            return 100.0
        return (self.covered / self.total) * 100.0

    @property
    def branch_percent(self) -> float:
        """Return branch coverage as a 0-100 percentage; 100 on empty input."""
        if self.branch_total <= 0:
            return 100.0
        return (self.branch_covered / self.branch_total) * 100.0
