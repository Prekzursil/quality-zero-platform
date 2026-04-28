#!/usr/bin/env python3
"""Run qlty zero."""

from __future__ import absolute_import

import argparse
import fnmatch
import os
import re
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
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _parse_args() -> argparse.Namespace:
    """Handle parse args."""
    parser = argparse.ArgumentParser(
        description="Run QLTY in fail-on-any-issue mode and emit a summary report."
    )
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--out-json", default=DEFAULT_JSON)
    parser.add_argument("--out-md", default=DEFAULT_MD)
    return parser.parse_args()


def _build_qlty_check_argv() -> List[str]:
    """Handle build qlty check argv."""
    return [
        "qlty",
        "check",
        "--all",
        "--fail-level",
        "note",
        "--summary",
    ]


def _build_qlty_smells_argv() -> List[str]:
    """Handle build qlty smells argv."""
    return [
        "qlty",
        "smells",
        "--all",
        "--quiet",
        "--no-snippets",
    ]


def _resolved_qlty_executable_path() -> str:
    """Handle resolved qlty executable path."""
    qlty_executable = shutil.which(_QLTY_EXECUTABLE)
    if not qlty_executable:
        raise FileNotFoundError(
            f"Unable to locate required executable: {_QLTY_EXECUTABLE}"
        )
    return qlty_executable


def _tail_lines(text: str, *, limit: int = 200) -> str:
    """Handle tail lines."""
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= limit:
        return "\n".join(lines)
    return "\n".join(lines[-limit:])


def _combine_output(stdout: str, stderr: str) -> str:
    """Handle combine output."""
    stdout_tail = _tail_lines(stdout)
    stderr_tail = _tail_lines(stderr)
    if stdout_tail and stderr_tail:
        return stdout_tail + "\n" + stderr_tail
    return stdout_tail or stderr_tail


def _extract_path_pattern(line: str) -> str | None:
    """Pull the ``path = "..."`` value out of one ``[[smells.exclude]]`` line."""
    match = re.match(r'path\s*=\s*"([^"]+)"', line)
    return match.group(1) if match else None


def _smells_exclude_lines(toml_path: Path) -> Iterable[str]:
    """Yield stripped non-empty lines from qlty.toml (or empty if missing)."""
    if not toml_path.is_file():
        return ()
    return (raw.strip() for raw in toml_path.read_text(encoding="utf-8").splitlines())


def _load_smells_exclude_patterns(repo_dir: Path) -> List[str]:
    """Parse qlty.toml for smells.exclude path patterns."""
    patterns: List[str] = []
    in_smells_exclude = False
    for line in _smells_exclude_lines(repo_dir / ".qlty" / "qlty.toml"):
        if line == "[[smells.exclude]]":
            in_smells_exclude = True
            continue
        if not in_smells_exclude:
            continue
        if line.startswith("path"):
            value = _extract_path_pattern(line)
            if value is not None:
                patterns.append(value)
            in_smells_exclude = False
        elif line.startswith("["):
            in_smells_exclude = False
    return patterns


def _filter_smells_output(output: str, exclude_patterns: List[str]) -> str:
    """Remove smells findings for files matching exclude patterns."""
    if not exclude_patterns:
        return output
    lines = output.splitlines()
    filtered: List[str] = []
    skip_block = False
    for line in lines:
        if line and not line[0].isspace():
            skip_block = any(fnmatch.fnmatch(line.strip(), p) for p in exclude_patterns)
        if not skip_block:
            filtered.append(line)
    return "\n".join(filtered)


def _smells_output_indicates_findings(output_tail: str) -> bool:
    """Handle smells output indicates findings."""
    normalized = _ANSI_ESCAPE_RE.sub("", output_tail).strip().lower()
    if not normalized:
        return False
    return normalized not in {"no smells", "no issues", "✔ no issues"}


def _render_md(payload: Mapping[str, Any]) -> str:
    """Handle render md."""
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
    """Handle cast mapping."""
    return value if isinstance(value, Mapping) else {}


def _run_qlty_check(repo_dir: Path) -> subprocess.CompletedProcess[str]:
    """Handle run qlty check."""
    executable_path = _resolved_qlty_executable_path()
    argv = [executable_path, "check", "--all", "--fail-level", "note", "--summary"]
    # Safe-by-construction: a fixed literal executable name, explicit argv,
    # shell=False, and an absolute executable path supplied separately.
    # nosemgrep
    return subprocess.run(  # noqa: S603  # nosec
        argv,
        executable=executable_path,
        cwd=repo_dir,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
    )


def _run_qlty_smells(repo_dir: Path) -> subprocess.CompletedProcess[str]:
    """Handle run qlty smells."""
    executable_path = _resolved_qlty_executable_path()
    argv = [executable_path, "smells", "--all", "--quiet", "--no-snippets"]
    # Safe-by-construction: a fixed literal executable name, explicit argv,
    # shell=False, and an absolute executable path supplied separately.
    # nosemgrep
    return subprocess.run(  # noqa: S603  # nosec
        argv,
        executable=executable_path,
        cwd=repo_dir,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
    )


def _smells_lane_has_findings(
    name: str,
    output_tail: str,
    smells_exclude_patterns: List[str] | None,
) -> bool:
    """Return whether the smells lane reports actionable findings."""
    if name != "smells":
        return False
    target = (
        _filter_smells_output(output_tail, smells_exclude_patterns)
        if smells_exclude_patterns
        else output_tail
    )
    return _smells_output_indicates_findings(target)


def _build_check_entry(
    name: str,
    argv: List[str],
    result: subprocess.CompletedProcess[str],
    smells_exclude_patterns: List[str] | None = None,
) -> Dict[str, Any]:
    """Handle build check entry."""
    output_tail = _combine_output(result.stdout or "", result.stderr or "")
    has_findings = _smells_lane_has_findings(name, output_tail, smells_exclude_patterns)
    # Combine the two fail conditions into a single test so Sonar's
    # python:S1871 doesn't flag the duplicate ``status = "fail"`` branches
    # — both routes ultimately mean "this lane failed".
    failed_check = int(result.returncode) != 0 and name != "smells"
    failed_smells = name == "smells" and has_findings
    status = "fail" if (failed_check or failed_smells) else "pass"
    return {
        "name": name,
        "status": status,
        "return_code": int(result.returncode),
        "command": argv,
        "output_tail": output_tail,
    }


def _build_payload(
    checks: Iterable[Mapping[str, Any]], *, status: str, return_code: int
) -> Dict[str, Any]:
    """Handle build payload."""
    check_list = [dict(check) for check in checks]
    output_chunks = [
        str(check.get("output_tail") or "").strip() for check in check_list
    ]
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
    """Handle working directory."""
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _write_payload(payload: Mapping[str, Any], *, out_json: str, out_md: str) -> int:
    """Handle write payload."""
    return write_report(
        payload,
        out_json=out_json,
        out_md=out_md,
        default_json=DEFAULT_JSON,
        default_md=DEFAULT_MD,
        render_md=_render_md,
    )


def _run_checks(repo_dir: Path) -> Tuple[List[Dict[str, Any]], int]:
    """Run ``qlty check`` and ``qlty smells``; return entries + propagated rc.

    The gate fails if either lane fails. ``qlty smells`` exits 0 even when
    findings are present (it is a report tool), so we synthesize a non-zero
    return code from the entry status. Earlier versions of this function
    silently exempted smell findings from the propagation step — the gate
    therefore stayed green even with duplication or complexity findings,
    which is exactly the kind of "config opt-out" that the platform's
    no-opt-out policy forbids.
    """
    entries: List[Dict[str, Any]] = []
    final_return_code = 0
    exclude_patterns = _load_smells_exclude_patterns(repo_dir)
    checks: List[Tuple[str, List[str], subprocess.CompletedProcess[str]]] = [
        ("check", _build_qlty_check_argv(), _run_qlty_check(repo_dir)),
        ("smells", _build_qlty_smells_argv(), _run_qlty_smells(repo_dir)),
    ]
    for name, argv, result in checks:
        entry = _build_check_entry(
            name, argv, result,
            smells_exclude_patterns=exclude_patterns if name == "smells" else None,
        )
        entries.append(entry)
        if final_return_code == 0 and entry["status"] != "pass":
            final_return_code = int(result.returncode) or 1
    return entries, final_return_code


def _build_missing_command_payload() -> Dict[str, Any]:
    """Handle build missing command payload."""
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
    """Handle main."""
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
