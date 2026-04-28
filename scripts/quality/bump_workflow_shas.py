#!/usr/bin/env python3
"""Phase-5 follow-up: fleet-wide reusable-workflow SHA bumper.

Discovered 2026-04-26 by auditing the QZP fleet: 14 of 14 consumer
repos pin pre-Phase-2 SHAs in their caller workflows
(``codecov-analytics.yml``, ``quality-zero-gate.yml``, etc.). The
drift-sync wave doesn't touch these — its templates render
``codecov.yml`` / ``ci.yml`` / etc, not the workflow callers.

This module is the pure logic that walks workflow text, identifies
``uses: Prekzursil/quality-zero-platform/.github/workflows/<name>.yml@<sha>``
references, and rewrites every SHA to a target. The companion
workflow (``bump-workflow-shas-wave.yml``, separate increment) uses
this to fan out across ``inventory/repos.yml`` and open a bump PR
on each consumer repo.

Public API:

* ``find_reusable_pins(text)`` — list of ``(workflow_name, sha)``
  tuples extracted from the text. Branch / tag pins are ignored
  (only 40-char hex SHAs count as canonical pins).
* ``bump_pins_to_target(text, target_sha)`` → ``(new_text, count)``.
  Refuses non-40-char-hex targets to protect against accidental
  branch-name pinning.
* ``bump_workflow_files(files, target_sha)`` → ``{path: {bumped, new_text}}``
  convenience wrapper for the workflow caller.
"""

from __future__ import absolute_import

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()


_QZP_PIN_RE = re.compile(
    r"Prekzursil/quality-zero-platform/\.github/workflows/"
    r"(?P<name>[A-Za-z0-9_.-]+\.yml)@(?P<sha>[0-9a-f]{40})\b",
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def find_reusable_pins(text: str) -> List[Tuple[str, str]]:
    """Return ``[(workflow_name, sha), ...]`` for every QZP pin in ``text``."""
    return [(m.group("name"), m.group("sha")) for m in _QZP_PIN_RE.finditer(text)]


def bump_pins_to_target(text: str, *, target_sha: str) -> Tuple[str, int]:
    """Rewrite every QZP pin in ``text`` to ``target_sha``.

    Returns ``(new_text, count)`` where ``count`` is the number of pins
    that were actually CHANGED (already-current pins don't increment).

    Raises ``ValueError`` for non-40-char-hex target SHAs to protect
    against accidental branch-name pinning.
    """
    if not _SHA_RE.match(target_sha or ""):
        raise ValueError(
            f"target_sha must be a 40-char hex SHA; got {target_sha!r}",
        )
    bumps = 0

    def _replace(match: "re.Match[str]") -> str:
        nonlocal bumps
        old_sha = match.group("sha")
        if old_sha == target_sha:
            return match.group(0)
        bumps += 1
        return (
            f"Prekzursil/quality-zero-platform/.github/workflows/"
            f"{match.group('name')}@{target_sha}"
        )

    new_text = _QZP_PIN_RE.sub(_replace, text)
    return new_text, bumps


def bump_workflow_files(
    files: Mapping[str, str], *, target_sha: str,
) -> Dict[str, Dict[str, Any]]:
    """Apply ``bump_pins_to_target`` to every ``{path: text}`` pair.

    Returns ``{path: {"bumped": int, "new_text": str}}``. Files with no
    QZP pins (``bumped == 0``) keep their original text in ``new_text``.
    """
    if not _SHA_RE.match(target_sha or ""):
        raise ValueError(
            f"target_sha must be a 40-char hex SHA; got {target_sha!r}",
        )
    results: Dict[str, Dict[str, Any]] = {}
    for path, text in files.items():
        new_text, count = bump_pins_to_target(text, target_sha=target_sha)
        results[path] = {"bumped": count, "new_text": new_text}
    return results


def _run_cli() -> None:  # pragma: no cover — ad-hoc CLI
    """Dispatch one bump-workflow-shas run from argv to stdout/stderr.

    Wrapped in a function so the loop variable ``path`` stays local and
    doesn't shadow the same-named loop variable in
    ``bump_workflow_files`` (pylint W0621 — see
    ``reference_pylint_c0103_module_main_block`` memory).
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-sha", required=True,
        help="40-char hex SHA to pin all QZP reusable workflows to.",
    )
    parser.add_argument(
        "--workflow-dir", default=".github/workflows",
        help="Directory whose .yml files should be bumped in-place.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write changes to disk (default: dry-run, prints a summary).",
    )
    args = parser.parse_args()

    workflow_dir = Path(args.workflow_dir)
    if not workflow_dir.is_dir():
        print(f"workflow dir not found: {workflow_dir}", file=sys.stderr)
        raise SystemExit(2)

    files = {
        str(p): p.read_text(encoding="utf-8")
        for p in sorted(workflow_dir.glob("*.yml"))
    }
    results = bump_workflow_files(files, target_sha=args.target_sha)

    summary = {
        path: result["bumped"] for path, result in results.items()
    }
    print(json.dumps(summary, indent=2, sort_keys=True))

    if args.apply:
        for path, result in results.items():
            if result["bumped"]:
                Path(path).write_text(result["new_text"], encoding="utf-8")
                print(f"wrote {path} ({result['bumped']} pin(s) bumped)",
                      file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover — ad-hoc CLI
    _run_cli()
