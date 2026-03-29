"""Shared string normalization helpers for quality tooling."""

from __future__ import absolute_import

from typing import Iterable, List


def dedupe_strings(items: Iterable[str | None]) -> List[str]:
    """Trim strings, drop empties, and preserve first-seen order."""
    normalized = (str(item or "").strip() for item in items)
    return list(dict.fromkeys(value for value in normalized if value))
