#!/usr/bin/env python3
"""Generate ruleset payload."""

from __future__ import absolute_import

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # pragma: no cover — direct CLI invocation

from scripts.quality.control_plane import (
    build_ruleset_payload,
    load_inventory,
    load_repo_profile,
)


def _parse_args() -> argparse.Namespace:
    """Handle parse args."""
    parser = argparse.ArgumentParser(description="Generate JSON ruleset payloads from control-plane profiles.")
    parser.add_argument("--inventory", default="")
    parser.add_argument("--repo-slug", action="append", default=[])
    parser.add_argument("--output-dir", default="generated/rulesets")
    return parser.parse_args()


def generate_rulesets(
    inventory: Dict[str, Any],
    output_dir: Path,
    *,
    repo_slugs: Sequence[str] | None = None,
    verbose: bool = True,
) -> List[Path]:
    """Render every requested profile to ``<output_dir>/<profile_id>.json``.

    Shared by the CLI and by ``build_dashboard_extras.py`` (the Lane-8
    ruleset-drift panel), which regenerates into a throwaway directory and
    byte-compares the result against the committed ``generated/rulesets``
    artifacts — the same determinism contract ``scripts/verify`` and
    ``fleet-health.yml`` enforce.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    slugs = repo_slugs if repo_slugs is not None else [item["slug"] for item in inventory["repos"]]
    written: List[Path] = []
    for repo_slug in slugs:
        profile = load_repo_profile(inventory, repo_slug)
        payload = build_ruleset_payload(profile)
        out_path = output_dir / f"{profile['profile_id']}.json"
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        if verbose:
            print(str(out_path))
        written.append(out_path)
    return written


def main() -> int:
    """Handle main."""
    args = _parse_args()
    # Empty --inventory (the default) means "the repo-root inventory".
    inventory = load_inventory(args.inventory) if args.inventory else load_inventory()
    generate_rulesets(inventory, Path(args.output_dir), repo_slugs=args.repo_slug or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
