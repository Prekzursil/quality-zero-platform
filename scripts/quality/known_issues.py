#!/usr/bin/env python3
"""Loader + validator for the ``known-issues/`` registry.

Phase 4 of ``docs/QZP-V2-DESIGN.md`` §6. QRv2 pulls these entries into
its Codex prompt so the remediation loop has the canonical fix for
every recurring false positive instead of rediscovering it per run.

Public surface:

* ``load_known_issues(registry_path)`` — walks ``<registry>/QZ-*.yml``
  and returns the parsed dicts, rejecting entries that fail the
  required-fields check.
* ``qrv2_prompt_entries(entries)`` — returns only the entries flagged
  ``feeds_qrv2: true`` and with a non-empty ``fix_snippet``.

Tests pin the schema contract so a malformed entry never leaks into
the QRv2 prompt.
"""

from __future__ import absolute_import

import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml  # type: ignore[import-untyped]


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()


REQUIRED_FIELDS = (
    "id",
    "title",
    "description",
    "affects",
    "feeds_qrv2",
    "verified_at",
)


class KnownIssueError(ValueError):
    """Raised when a registry file is missing a required field."""


def _validate_entry(entry: Dict[str, Any], path: Path) -> None:
    """Fail loudly if ``entry`` is missing a required field."""
    missing = [f for f in REQUIRED_FIELDS if f not in entry]
    if missing:
        raise KnownIssueError(
            f"{path}: missing required fields: {', '.join(sorted(missing))}"
        )
    if entry.get("feeds_qrv2") is True and not str(
        entry.get("fix_snippet") or ""
    ).strip():
        raise KnownIssueError(
            f"{path}: entries with ``feeds_qrv2: true`` must include a "
            f"non-empty ``fix_snippet``"
        )


def load_known_issues(registry_path: Path) -> List[Dict[str, Any]]:
    """Return every parsed entry under ``registry_path``.

    Ordering is stable (sorted by id) so QRv2's prompt is deterministic.
    Skips non-YAML files and the README.
    """
    entries: List[Dict[str, Any]] = []
    if not registry_path.is_dir():
        return entries
    for path in sorted(registry_path.glob("QZ-*.yml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise KnownIssueError(
                f"{path}: top-level YAML must be a mapping, got {type(payload).__name__}"
            )
        _validate_entry(payload, path)
        entries.append(payload)
    entries.sort(key=lambda e: str(e.get("id", "")))
    return entries


def qrv2_prompt_entries(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return entries that should feed QRv2's Codex prompt."""
    return [
        e for e in entries
        if bool(e.get("feeds_qrv2")) is True
        and str(e.get("fix_snippet") or "").strip()
    ]


if __name__ == "__main__":  # pragma: no cover — ad-hoc CLI
    root = Path(__file__).resolve().parents[2] / "known-issues"
    for entry in load_known_issues(root):
        print(entry.get("id"), "-", entry.get("title"))
