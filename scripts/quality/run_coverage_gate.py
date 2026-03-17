#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report

DEFAULT_COVERAGE_JSON = "coverage-100/coverage.json"
DEFAULT_COVERAGE_MD = "coverage-100/coverage.md"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run repo-specific coverage collection and assert the configured gate.")
    parser.add_argument("--profile-json", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--platform-dir", default="")
    return parser.parse_args()


def _coverage_mode(coverage: dict, event_name: str) -> str:
    assert_mode = coverage.get("assert_mode", {})
    if event_name in assert_mode:
        return str(assert_mode[event_name])
    return str(assert_mode.get("default", "enforce"))


def _run_shell(command: str, *, shell_name: str, cwd: Path) -> None:
    if not command.strip():
        return
    if shell_name == "pwsh":
        cmd = ["pwsh", "-NoLogo", "-Command", command]
    else:
        cmd = ["bash", "-lc", command]
    subprocess.run(cmd, cwd=cwd, check=True)


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
        "- None",
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
    coverage = profile.get("coverage", {})

    _run_shell(str(coverage.get("command", "")), shell_name=str(coverage.get("shell", "bash")), cwd=repo_dir)

    mode = _coverage_mode(coverage, args.event_name)
    if mode == "evidence_only":
        note = str(coverage.get("evidence_note", "")).strip() or "100/100 hard gate is enforced on protected branch pushes."
        return _write_evidence_only_report(note)

    cmd = [sys.executable, str(platform_dir / "scripts" / "quality" / "assert_coverage_100.py")]
    for item in coverage.get("inputs", []):
        flag = "--xml" if item.get("format") == "xml" else "--lcov"
        cmd.extend([flag, f"{item['name']}={item['path']}"])
    for item in coverage.get("require_sources", []):
        cmd.extend(["--require-source", str(item)])
    cmd.extend(["--min-percent", str(coverage.get("min_percent", 100.0))])
    cmd.extend(["--out-json", DEFAULT_COVERAGE_JSON, "--out-md", DEFAULT_COVERAGE_MD])
    subprocess.run(cmd, cwd=repo_dir, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
