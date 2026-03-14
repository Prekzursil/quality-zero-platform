#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run `codex exec` non-interactively from a trusted runner.")
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--output-last-message", required=True)
    parser.add_argument("--json-log", default="")
    parser.add_argument("--sandbox", default="workspace-write")
    parser.add_argument("--config", action="append", default=[])
    parser.add_argument("--profile", default="")
    parser.add_argument("--model", default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    prompt_text = Path(args.prompt_file).read_text(encoding="utf-8")

    cmd = [
        "codex",
        "exec",
        "--full-auto",
        "-C",
        str(Path(args.repo_dir).resolve()),
        "-s",
        args.sandbox,
        "--json",
        "-o",
        str(Path(args.output_last_message).resolve()),
        "-",
    ]
    if args.profile:
        cmd.extend(["-p", args.profile])
    if args.model:
        cmd.extend(["-m", args.model])
    for item in args.config:
        cmd.extend(["-c", item])

    completed = subprocess.run(
        cmd,
        input=prompt_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if args.json_log:
        log_path = Path(args.json_log)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(completed.stdout, encoding="utf-8")

    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)

    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
