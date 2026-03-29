#!/usr/bin/env python3
"""Validate control plane."""

from __future__ import absolute_import

import argparse
import json
from pathlib import Path
import sys
from typing import List

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.control_plane import build_ruleset_payload, load_inventory, load_repo_profile, validate_profile


def _parse_args() -> argparse.Namespace:
    """Handle parse args."""
    parser = argparse.ArgumentParser(description="Validate all enrolled control-plane repo profiles.")
    parser.add_argument("--inventory", default="")
    parser.add_argument("--write-generated", default="")
    return parser.parse_args()


def main() -> int:
    """Handle main."""
    args = _parse_args()
    inventory = load_inventory(args.inventory) if args.inventory else load_inventory()
    generated_dir = Path(args.write_generated) if args.write_generated else None
    if generated_dir is not None:
        generated_dir.mkdir(parents=True, exist_ok=True)

    findings: List[str] = []
    for repo_entry in inventory["repos"]:
        profile = load_repo_profile(inventory, repo_entry["slug"])
        findings.extend(validate_profile(profile))
        if generated_dir is not None:
            payload = build_ruleset_payload(profile)
            output_path = generated_dir / f"{profile['profile_id']}.json"
            output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if findings:
        for finding in findings:
            print(finding)
        return 1

    print(f"Validated {len(inventory['repos'])} repo profiles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
