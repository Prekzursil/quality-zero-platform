#!/usr/bin/env python3
from __future__ import absolute_import

import argparse
import json
import os
import subprocess  # nosec B404
import sys
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, cast

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the required-checks probe for the quality-zero gate.")
    parser.add_argument("--profile-json", required=True)
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--platform-dir", default="")
    parser.add_argument("--out-json", default="quality-zero-gate/required-checks.json")
    parser.add_argument("--out-md", default="quality-zero-gate/required-checks.md")
    return parser.parse_args()


def _required_contexts(profile: Dict[str, Any]) -> List[str]:
    contexts = profile.get("active_required_contexts")
    if isinstance(contexts, list):
        return [str(item).strip() for item in contexts if str(item).strip()]
    fallback = profile.get("required_contexts", {})
    if isinstance(fallback, dict):
        target = fallback.get("target", [])
        if isinstance(target, list):
            return [str(item).strip() for item in target if str(item).strip()]
    raise SystemExit("Resolved profile did not include active_required_contexts")


def _build_argv(profile: Dict[str, Any], sha: str, *args: Any, **kwargs: Any) -> List[str]:
    if args:
        if len(args) != 3:
            raise TypeError("_build_argv expects platform_dir and output paths or keyword arguments")
        platform_dir, out_json, out_md = args
    else:
        try:
            platform_dir = kwargs.pop("platform_dir")
            out_json = kwargs.pop("out_json")
            out_md = kwargs.pop("out_md")
        except KeyError as exc:  # pragma: no cover - defensive contract guard
            raise TypeError(f"Missing required argv parameter: {exc.args[0]}") from exc
        if kwargs:
            raise TypeError(f"Unexpected _build_argv parameters: {', '.join(sorted(kwargs))}")

    platform_dir_text = str(platform_dir)
    script_path = (
        str(PureWindowsPath(platform_dir_text) / "scripts" / "quality" / "check_required_checks.py")
        if "\\" in platform_dir_text and ":" in platform_dir_text
        else str(Path(platform_dir_text) / "scripts" / "quality" / "check_required_checks.py")
    )

    argv = [
        script_path,
        "--repo",
        str(profile["slug"]),
        "--sha",
        sha,
        "--out-json",
        str(out_json),
        "--out-md",
        str(out_md),
    ]
    for context in _required_contexts(profile):
        argv.extend(["--required-context", context])
    return argv


def main() -> int:
    args = _parse_args()
    profile = json.loads(Path(args.profile_json).read_text(encoding="utf-8"))
    sha = (os.environ.get("TARGET_SHA", "").strip() or os.environ.get("GITHUB_SHA", "").strip())
    if not sha:
        raise SystemExit("TARGET_SHA or GITHUB_SHA is required")
    repo_dir = Path(args.repo_dir).resolve()
    platform_dir = Path(args.platform_dir).resolve() if args.platform_dir else Path(__file__).resolve().parents[2]
    argv = _build_argv(cast(Dict[str, Any], profile), sha, platform_dir=platform_dir, out_json=args.out_json, out_md=args.out_md)
    subprocess.run(argv, cwd=repo_dir, check=True)  # nosec B603
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
