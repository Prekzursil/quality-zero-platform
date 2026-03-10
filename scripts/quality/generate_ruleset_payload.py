#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.control_plane import build_ruleset_payload, load_inventory, load_repo_profile


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate JSON ruleset payloads from control-plane profiles.")
    parser.add_argument("--inventory", default="")
    parser.add_argument("--repo-slug", action="append", default=[])
    parser.add_argument("--output-dir", default="generated/rulesets")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    inventory = load_inventory(args.inventory) if args.inventory else load_inventory()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    repo_slugs = args.repo_slug or [item["slug"] for item in inventory["repos"]]
    for repo_slug in repo_slugs:
        profile = load_repo_profile(inventory, repo_slug)
        payload = build_ruleset_payload(profile)
        out_path = output_dir / f"{profile['profile_id']}.json"
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
