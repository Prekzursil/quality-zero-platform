#!/usr/bin/env python3
from __future__ import absolute_import

import argparse
import re
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import List

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._:/=-]+$")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run `codex exec` non-interactively from a trusted runner.")
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--output-last-message", required=True)
    parser.add_argument("--json-log", default="")
    parser.add_argument("--sandbox", default="workspace-write", choices=["read-only", "workspace-write", "danger-full-access"])
    parser.add_argument("--config", action="append", default=[])
    parser.add_argument("--profile", default="")
    parser.add_argument("--model", default="")
    return parser.parse_args()


def _validate_cli_token(value: str, *, flag_name: str) -> str:
    if not value:
        raise ValueError(f"{flag_name} cannot be empty")
    if any(character in value for character in ("\0", "\r", "\n")):
        raise ValueError(f"{flag_name} contains unsupported control characters")
    if not _TOKEN_PATTERN.fullmatch(value):
        raise ValueError(f"{flag_name} contains unsupported characters")
    return value


def _resolved_codex_executable() -> str:
    codex_executable = shutil.which("codex")
    if not codex_executable:
        raise FileNotFoundError("Unable to locate required executable: codex")
    return codex_executable


def _resolved_repo_dir(args: argparse.Namespace) -> str:
    return str(Path(args.repo_dir).resolve())


def _resolved_output_path(args: argparse.Namespace) -> str:
    return str(Path(args.output_last_message).resolve())


def _validated_sandbox(args: argparse.Namespace) -> str:
    return _validate_cli_token(args.sandbox, flag_name="--sandbox")


def _validated_profile_args(args: argparse.Namespace) -> List[str]:
    if not args.profile:
        return []
    return ["-p", _validate_cli_token(args.profile, flag_name="--profile")]


def _validated_model_args(args: argparse.Namespace) -> List[str]:
    if not args.model:
        return []
    return ["-m", _validate_cli_token(args.model, flag_name="--model")]


def _validated_config_args(args: argparse.Namespace) -> List[str]:
    config_args: List[str] = []
    for item in args.config:
        config_args.extend(["-c", _validate_cli_token(item, flag_name="--config")])
    return config_args


def build_codex_command(args: argparse.Namespace) -> List[str]:
    """Build the fixed codex argv list from resolved, validated inputs."""
    cmd = [
        _resolved_codex_executable(),
        "exec",
        "--full-auto",
        "-C",
        _resolved_repo_dir(args),
        "-s",
        _validated_sandbox(args),
        "--json",
        "-o",
        _resolved_output_path(args),
        "-",
    ]
    cmd.extend(_validated_profile_args(args))
    cmd.extend(_validated_model_args(args))
    cmd.extend(_validated_config_args(args))
    return cmd


def _run_codex_exec(args: argparse.Namespace, prompt_text: str) -> subprocess.CompletedProcess:
    """Run codex with a static literal argv list and prompt text passed via stdin."""
    profile_args = _validated_profile_args(args)
    model_args = _validated_model_args(args)
    config_args = _validated_config_args(args)
    # Safe-by-construction: explicit argv, shell=False, an absolute executable path,
    # validated tokens as plain arguments, and prompt content flowing only through stdin.
    return subprocess.run(  # nosec B603
        [
            _resolved_codex_executable(),
            "exec",
            "--full-auto",
            "-C",
            _resolved_repo_dir(args),
            "-s",
            _validated_sandbox(args),
            "--json",
            "-o",
            _resolved_output_path(args),
            "-",
            *profile_args,
            *model_args,
            *config_args,
        ],
        input=prompt_text,
        text=True,
        capture_output=True,
        shell=False,
        check=False,
    )


def main() -> int:
    args = _parse_args()
    prompt_text = Path(args.prompt_file).read_text(encoding="utf-8")

    completed = _run_codex_exec(args, prompt_text)
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
