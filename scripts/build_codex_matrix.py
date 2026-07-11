#!/usr/bin/env python3
"""Build the Codex fleet-sweep dispatch plan from ``codex-targets.yml``.

Reads the shared fleet config and emits, on stdout, the list of ``(repo, task)``
dispatch entries -- one per task of each ENABLED target -- with per-repo
overrides merged over the top-level ``defaults`` block. The Codex fleet wave
(``.github/workflows/codex-fleet-wave.yml``) consumes this to drive its serial,
one-lane dispatch loop; ``--format matrix`` additionally wraps the entries as a
GitHub Actions ``matrix`` object for anyone who prefers a matrix strategy.

Contract (see codex-targets.yml):

    defaults: {model: gpt-5.6-sol, effort: max, max_changed_files: 40}
    targets:
      - repo: Prekzursil/Reframe
        enabled: true
        tasks: ["Documentation only: ..."]
        max_changed_files: 25   # optional per-repo override

Each emitted entry is a flat object::

    {"repo": "...", "task": "...", "model": "...", "effort": "...",
     "max_changed_files": 25}

Pure standard library plus PyYAML. No network, no side effects.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dependency guard
    sys.stderr.write("error: PyYAML is required (pip install pyyaml)\n")
    raise SystemExit(2) from None

# Per-repo keys that may override the top-level ``defaults`` block.
OVERRIDABLE = ("model", "effort", "max_changed_files")
# Last-resort defaults if the config omits a ``defaults`` block entirely.
FALLBACK_DEFAULTS: dict[str, Any] = {
    "model": "gpt-5.6-sol",
    "effort": "max",
    "max_changed_files": 40,
}


def normalize_repo(repo: str) -> str:
    """Return the bare ``name`` from ``owner/name`` (case-folded, for matching)."""
    return repo.split("/", 1)[-1].strip().lower()


def _coerce_cap(value: Any, fallback: int) -> int:
    """Coerce a ``max_changed_files`` value to a positive int, else ``fallback``."""
    try:
        cap = int(value)
    except (TypeError, ValueError):
        return fallback
    return cap if cap > 0 else fallback


def load_config(path: Path) -> dict[str, Any]:
    """Read and parse the fleet config, exiting non-zero on any structural error."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"error: cannot read config {path}: {exc}\n")
        raise SystemExit(2) from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        sys.stderr.write(f"error: invalid YAML in {path}: {exc}\n")
        raise SystemExit(2) from exc
    if not isinstance(data, dict):
        sys.stderr.write(f"error: {path} must be a YAML mapping\n")
        raise SystemExit(2)
    return data


def build_entries(config: dict[str, Any], only_repo: str | None = None) -> list[dict[str, Any]]:
    """Expand the config into a flat list of dispatch entries.

    A full sweep (``only_repo`` is ``None``) includes every target with
    ``enabled: true`` that has at least one task. An explicit single-repo
    dispatch (``only_repo`` set) selects that one repo by name and BYPASSES the
    ``enabled`` flag -- naming a repo in a manual run is an intentional override
    -- but still requires the repo to have tasks defined.
    """
    raw_defaults = config.get("defaults") or {}
    if not isinstance(raw_defaults, dict):
        sys.stderr.write("error: 'defaults' must be a mapping\n")
        raise SystemExit(2)
    defaults = {**FALLBACK_DEFAULTS, **raw_defaults}
    default_cap = _coerce_cap(defaults.get("max_changed_files"), 40)

    targets = config.get("targets") or []
    if not isinstance(targets, list):
        sys.stderr.write("error: 'targets' must be a list\n")
        raise SystemExit(2)

    wanted = normalize_repo(only_repo) if only_repo else None
    matched_repo = False
    entries: list[dict[str, Any]] = []

    for target in targets:
        if not isinstance(target, dict):
            continue
        repo = str(target.get("repo") or "").strip()
        if not repo:
            continue

        if wanted is not None:
            if normalize_repo(repo) != wanted:
                continue
            matched_repo = True
        elif not bool(target.get("enabled", False)):
            continue

        tasks = target.get("tasks") or []
        if not isinstance(tasks, list):
            continue

        cap = _coerce_cap(target.get("max_changed_files"), default_cap)
        model = str(target.get("model", defaults["model"]))
        effort = str(target.get("effort", defaults["effort"]))

        for task in tasks:
            task_text = str(task).strip()
            if not task_text:
                continue
            entries.append(
                {
                    "repo": repo,
                    "task": task_text,
                    "model": model,
                    "effort": effort,
                    "max_changed_files": cap,
                }
            )

    if wanted is not None and not matched_repo:
        sys.stderr.write(f"warning: --only-repo '{only_repo}' matched no target in the config\n")
    elif wanted is not None and not entries:
        sys.stderr.write(f"warning: --only-repo '{only_repo}' has no tasks defined; nothing to do\n")
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the Codex fleet-sweep dispatch plan from codex-targets.yml.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("codex-targets.yml"),
        help="Path to codex-targets.yml (default: ./codex-targets.yml).",
    )
    parser.add_argument(
        "--only-repo",
        default=None,
        help=("Restrict to a single repo (owner/name or bare name); bypasses the 'enabled' flag."),
    )
    parser.add_argument(
        "--format",
        choices=("array", "matrix"),
        default="array",
        help='Output shape: "array" (JSON list, default) or "matrix" '
        '({"include": [...]}) for a GitHub Actions matrix strategy.',
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output.",
    )
    args = parser.parse_args(argv)

    only = args.only_repo.strip() if args.only_repo and args.only_repo.strip() else None
    config = load_config(args.config)
    entries = build_entries(config, only_repo=only)

    payload: Any = {"include": entries} if args.format == "matrix" else entries
    json.dump(payload, sys.stdout, indent=2 if args.pretty else None, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
