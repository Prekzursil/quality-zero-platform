"""Severity ordering for canonical findings (per design §A.4.2)."""
from __future__ import absolute_import

from typing import Final, Sequence, Tuple

SEVERITY_ORDER: Final[Tuple[str, ...]] = ("critical", "high", "medium", "low", "info")


def max_severity(severities: Sequence[str]) -> str:
    """Return the HIGHEST severity from the input sequence.

    Higher severity = lower index in SEVERITY_ORDER.
    Raises ValueError on empty input or unknown severities.
    """
    if not severities:
        raise ValueError("max_severity requires at least one severity")
    for severity in severities:
        if severity not in SEVERITY_ORDER:
            raise ValueError(f"Unknown severity: {severity!r}")
    return min(severities, key=SEVERITY_ORDER.index)
