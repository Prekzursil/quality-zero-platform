#!/usr/bin/env python3
from __future__ import absolute_import

import argparse
import json
import os
import subprocess  # nosec B404
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, cast

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # pragma: no cover

from scripts.quality import assert_coverage_100
from scripts.quality.common import (
    DEFAULT_COVERAGE_JSON,
    DEFAULT_COVERAGE_MD,
    NONE_BULLET,
    utc_timestamp,
    write_report,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run repo-specific coverage collection and assert the configured gate.")
    parser.add_argument("--profile-json", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--platform-dir", default="")
    return parser.parse_args()


def _coverage_mode(coverage: Dict[str, Any], event_name: str) -> str:
    assert_mode = cast(Dict[str, Any], coverage.get("assert_mode", {}))
    if event_name in assert_mode:
        return str(assert_mode[event_name])
    return str(assert_mode.get("default", "enforce"))


def _run_shell(command: str, *, shell_name: str, cwd: Path) -> None:
    if not command.strip():
        return
    shell_argv = ["pwsh", "-NoLogo", "-Command", "-"] if shell_name == "pwsh" else ["bash", "-s"]
    # Safe-by-construction: the interpreter argv is static, shell=False prevents interpolation,
    # and the repo-owned profile command is passed via stdin instead of becoming dynamic argv.
    subprocess.run(  # nosec B603
        shell_argv,
        cwd=cwd,
        input=command,
        text=True,
        shell=False,
        check=True,
    )


def _build_assert_coverage_argv(coverage: Dict[str, Any], platform_dir: Path) -> List[str]:
    cmd = [str(platform_dir / "scripts" / "quality" / "assert_coverage_100.py")]
    for item in cast(List[Dict[str, Any]], coverage.get("inputs", [])):
        flag = "--xml" if item.get("format") == "xml" else "--lcov"
        cmd.extend([flag, f"{item['name']}={item['path']}"])
    for item in cast(List[Any], coverage.get("require_sources", [])):
        cmd.extend(["--require-source", str(item)])
    cmd.extend(["--min-percent", str(coverage.get("min_percent", 100.0))])
    cmd.extend(["--out-json", DEFAULT_COVERAGE_JSON, "--out-md", DEFAULT_COVERAGE_MD])
    return cmd


@contextmanager
def _working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)
def _run_assert_coverage_100(coverage: Dict[str, Any], *, repo_dir: Path, platform_dir: Path) -> int:
    argv = _build_assert_coverage_argv(coverage, platform_dir)
    previous_argv = sys.argv
    sys.argv = argv
    try:
        with _working_directory(repo_dir):
            return assert_coverage_100.main()
    finally:
        sys.argv = previous_argv


def _render_evidence_md(payload: dict) -> str:
    lines = [
        "# Coverage 100 Gate",
        "",
        f"- Status: `{payload['status']}`",
        f"- Mode: `{payload['mode']}`",
        f"- Note: `{payload['note']}`",
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
        "",
        "## Findings",
        NONE_BULLET,
    ]
    return "\n".join(lines) + "\n"


def _write_evidence_only_report(note: str) -> int:
    payload = {
        "status": "pass",
        "mode": "evidence_only",
        "note": note,
        "timestamp_utc": utc_timestamp(),
    }
    return write_report(
        payload,
        out_json=DEFAULT_COVERAGE_JSON,
        out_md=DEFAULT_COVERAGE_MD,
        default_json=DEFAULT_COVERAGE_JSON,
        default_md=DEFAULT_COVERAGE_MD,
        render_md=_render_evidence_md,
    )


def main() -> int:
    args = _parse_args()
    profile = json.loads(Path(args.profile_json).read_text(encoding="utf-8"))
    repo_dir = Path(args.repo_dir).resolve()
    platform_dir = Path(args.platform_dir).resolve() if args.platform_dir else Path(__file__).resolve().parents[2]
    coverage = cast(Dict[str, Any], profile.get("coverage", {}))

    _run_shell(str(coverage.get("command", "")), shell_name=str(coverage.get("shell", "bash")), cwd=repo_dir)

    mode = _coverage_mode(coverage, args.event_name)
    if mode == "evidence_only":
        note = str(coverage.get("evidence_note", "")).strip() or "100/100 hard gate is enforced on protected branch pushes."
        return _write_evidence_only_report(note)

    return _run_assert_coverage_100(coverage, repo_dir=repo_dir, platform_dir=platform_dir)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())  # pragma: no cover
