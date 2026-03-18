#!/usr/bin/env python3
from __future__ import absolute_import

import argparse
import os
import shutil
import subprocess  # nosec B404
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report


DEFAULT_JSON = "qlty-zero/qlty-zero.json"
DEFAULT_MD = "qlty-zero/qlty-zero.md"
_QLTY_EXECUTABLE = "qlty"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run QLTY in fail-on-any-issue mode and emit a summary report."
    )
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--out-json", default=DEFAULT_JSON)
    parser.add_argument("--out-md", default=DEFAULT_MD)
    return parser.parse_args()


def _build_qlty_check_argv() -> List[str]:
    return [
        "qlty",
        "check",
        "--all",
        "--fail-level",
        "note",
        "--summary",
    ]


def _build_qlty_smells_argv() -> List[str]:
    return [
        "qlty",
        "smells",
        "--all",
        "--quiet",
        "--no-snippets",
    ]


def _resolved_qlty_executable_path() -> str:
    qlty_executable = shutil.which(_QLTY_EXECUTABLE)
    if not qlty_executable:
        raise FileNotFoundError(f"Unable to locate required executable: {_QLTY_EXECUTABLE}")
    return qlty_executable


def _tail_lines(text: str, *, limit: int = 200) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= limit:
        return "\n".join(lines)
    return "\n".join(lines[-limit:])


def _combine_output(stdout: str, stderr: str) -> str:
    stdout_tail = _tail_lines(stdout)
    stderr_tail = _tail_lines(stderr)
    if stdout_tail and stderr_tail:
        return stdout_tail + "\n" + stderr_tail
    return stdout_tail or stderr_tail


def _smells_output_indicates_findings(output_tail: str) -> bool:
    normalized = output_tail.strip().lower()
    if not normalized:
        return False
    return normalized not in {"no smells", "no issues"}


def _render_md(payload: Mapping[str, Any]) -> str:
    lines = [
        "# QLTY Zero",
        "",
        "- Status: `" + str(payload["status"]) + "`",
        "- Return code: `" + str(payload["return_code"]) + "`",
        "- Timestamp (UTC): `" + str(payload["timestamp_utc"]) + "`",
        "",
    ]
    for check in payload.get("checks", []):
        section = cast_mapping(check)
        lines.extend(
            [
                "## " + str(section["name"]),
                "",
                "- Status: `" + str(section["status"]) + "`",
                "- Return code: `" + str(section["return_code"]) + "`",
                "- Command: `" + str(section["command"]) + "`",
                "",
                "### Output (tail)",
                "",
            ]
        )
        output_tail = str(section.get("output_tail") or "")
        if output_tail:
            lines.append("```")
            lines.append(output_tail)
            lines.append("```")
        else:
            lines.append("- None")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def cast_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _run_qlty_check(repo_dir: Path) -> subprocess.CompletedProcess[str]:
    command = _build_qlty_check_argv()
    command[0] = _resolved_qlty_executable_path()
    return subprocess.run(  # nosec B603,B607
        command,
        cwd=repo_dir,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
    )


def _run_qlty_smells(repo_dir: Path) -> subprocess.CompletedProcess[str]:
    command = _build_qlty_smells_argv()
    command[0] = _resolved_qlty_executable_path()
    return subprocess.run(  # nosec B603,B607
        command,
        cwd=repo_dir,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
    )


def _build_check_entry(name: str, argv: List[str], result: subprocess.CompletedProcess[str]) -> Dict[str, Any]:
    output_tail = _combine_output(result.stdout or "", result.stderr or "")
    status = "pass"
    if int(result.returncode) != 0 or (name == "smells" and _smells_output_indicates_findings(output_tail)):
        status = "fail"
    return {
        "name": name,
        "status": status,
        "return_code": int(result.returncode),
        "command": argv,
        "output_tail": output_tail,
    }


def _build_payload(checks: Iterable[Mapping[str, Any]], *, status: str, return_code: int) -> Dict[str, Any]:
    check_list = [dict(check) for check in checks]
    output_chunks = [str(check.get("output_tail") or "").strip() for check in check_list]
    output_tail = "\n".join(chunk for chunk in output_chunks if chunk)
    return {
        "status": status,
        "return_code": int(return_code),
        "timestamp_utc": utc_timestamp(),
        "commands": [list(check.get("command", [])) for check in check_list],
        "checks": check_list,
        "output_tail": output_tail,
    }


@contextmanager
def _working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _write_payload(payload: Mapping[str, Any], *, out_json: str, out_md: str) -> int:
    return write_report(
        payload,
        out_json=out_json,
        out_md=out_md,
        default_json=DEFAULT_JSON,
        default_md=DEFAULT_MD,
        render_md=_render_md,
    )


def _run_checks(repo_dir: Path) -> Tuple[List[Dict[str, Any]], int]:
    entries: List[Dict[str, Any]] = []
    final_return_code = 0
    checks: List[Tuple[str, List[str], subprocess.CompletedProcess[str]]] = [
        ("check", _build_qlty_check_argv(), _run_qlty_check(repo_dir)),
        ("smells", _build_qlty_smells_argv(), _run_qlty_smells(repo_dir)),
    ]
    for name, argv, result in checks:
        entry = _build_check_entry(name, argv, result)
        entries.append(entry)
        if final_return_code == 0 and entry["status"] != "pass":
            final_return_code = int(result.returncode) or 1
    return entries, final_return_code


def _build_missing_command_payload() -> Dict[str, Any]:
    missing_message = "Failed to execute qlty: command not found"
    check_argv = _build_qlty_check_argv()
    smells_argv = _build_qlty_smells_argv()
    checks = [
        {
            "name": "check",
            "status": "error",
            "return_code": 1,
            "command": check_argv,
            "output_tail": missing_message,
        },
        {
            "name": "smells",
            "status": "error",
            "return_code": 1,
            "command": smells_argv,
            "output_tail": missing_message,
        },
    ]
    return _build_payload(checks, status="error", return_code=1)


def main() -> int:
    args = _parse_args()
    repo_dir = Path(args.repo_dir).resolve()

    with _working_directory(repo_dir):
        try:
            checks, return_code = _run_checks(repo_dir)
            status = "pass" if return_code == 0 else "fail"
            payload = _build_payload(checks, status=status, return_code=return_code)
        except FileNotFoundError:
            payload = _build_missing_command_payload()
            report_code = _write_payload(
                payload,
                out_json=args.out_json,
                out_md=args.out_md,
            )
            return report_code if report_code != 0 else 1

        report_code = _write_payload(
            payload,
            out_json=args.out_json,
            out_md=args.out_md,
        )
        if report_code != 0:
            return report_code
        return 0 if payload["return_code"] == 0 else int(payload["return_code"])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())  # pragma: no cover
