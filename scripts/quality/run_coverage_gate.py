#!/usr/bin/env python3
from __future__ import absolute_import

import argparse
import http.client
import json
import os
import subprocess  # nosec B404
import sys
import zipfile
from io import BytesIO
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, cast
from urllib.parse import urlparse

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
from scripts.security_helpers import normalize_https_url


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
    # Safe-by-construction: the interpreter argv is static, shell=False prevents interpolation,
    # and the repo-owned profile command is passed via stdin instead of becoming dynamic argv.
    if shell_name == "pwsh":
        if _path_exists(r"C:\Program Files\PowerShell\7\pwsh.exe"):
            subprocess.run(  # nosec B603
                [r"C:\Program Files\PowerShell\7\pwsh.exe", "-NoLogo", "-Command", "-"],
                cwd=cwd,
                input=command,
                text=True,
                shell=False,
                check=True,
            )
            return
        if _path_exists("/usr/bin/pwsh"):
            subprocess.run(  # nosec B603
                ["/usr/bin/pwsh", "-NoLogo", "-Command", "-"],
                cwd=cwd,
                input=command,
                text=True,
                shell=False,
                check=True,
            )
            return
        raise FileNotFoundError("Unable to locate required shell executable: pwsh")
    if _path_exists("/usr/bin/bash"):
        subprocess.run(  # nosec B603
            ["/usr/bin/bash", "-s"],
            cwd=cwd,
            input=command,
            text=True,
            shell=False,
            check=True,
        )
        return
    if _path_exists("/bin/bash"):
        subprocess.run(  # nosec B603
            ["/bin/bash", "-s"],
            cwd=cwd,
            input=command,
            text=True,
            shell=False,
            check=True,
        )
        return
    raise FileNotFoundError("Unable to locate required shell executable: bash")


def _path_exists(raw_path: str) -> bool:
    return Path(raw_path).exists()


def _build_assert_coverage_argv(coverage: Dict[str, Any], platform_dir: Path) -> List[str]:
    cmd = [str(platform_dir / "scripts" / "quality" / "assert_coverage_100.py")]
    for item in cast(List[Dict[str, Any]], coverage.get("inputs", [])):
        flag = "--xml" if item.get("format") == "xml" else "--lcov"
        cmd.extend([flag, f"{item['name']}={item['path']}"])
    for item in cast(List[Any], coverage.get("require_sources", [])):
        cmd.extend(["--require-source", str(item)])
    cmd.extend(["--min-percent", str(coverage.get("min_percent", 100.0))])
    if coverage.get("branch_min_percent") is not None:
        cmd.extend(["--branch-min-percent", str(coverage.get("branch_min_percent"))])
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


def _combined_coverage_percent(payload: Dict[str, Any]) -> float:
    components = payload.get("components", [])
    if not isinstance(components, list):
        return 100.0
    total = sum(int(item.get("total", 0)) for item in components if isinstance(item, dict))
    covered = sum(int(item.get("covered", 0)) for item in components if isinstance(item, dict))
    return 100.0 if total <= 0 else (covered / total) * 100.0


def _collect_current_coverage_payload(coverage: Dict[str, Any], *, repo_dir: Path, platform_dir: Path) -> Dict[str, Any]:
    result = _run_assert_coverage_100(coverage, repo_dir=repo_dir, platform_dir=platform_dir)
    if result not in {0, 1}:
        raise RuntimeError(f"coverage assertion returned unexpected exit code {result}")
    coverage_path = repo_dir / DEFAULT_COVERAGE_JSON
    return json.loads(coverage_path.read_text(encoding="utf-8"))


def _download_bytes(url: str, token: str) -> bytes:
    safe_url = normalize_https_url(url, allowed_hosts={"api.github.com"})
    parsed = urlparse(safe_url)
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    connection = http.client.HTTPSConnection(parsed.netloc, timeout=30)
    try:
        connection.request(
            "GET",
            path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "quality-zero-platform",
            },
        )
        response = connection.getresponse()
        return response.read()
    finally:
        connection.close()


def _github_api_token() -> str:
    token = (os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")).strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN or GH_TOKEN is required for non_regression coverage mode.")
    return token


def _find_successful_run_id(workflow_runs: List[Dict[str, Any]], workflow_name: str) -> int | None:
    return next(
        (
            int(item["id"])
            for item in workflow_runs
            if item.get("name") == workflow_name and item.get("conclusion") == "success"
        ),
        None,
    )


def _find_artifact_by_name(artifacts: List[Dict[str, Any]], name: str) -> Dict[str, Any] | None:
    return next((item for item in artifacts if item.get("name") == name), None)


def _load_baseline_coverage_payload(profile: Dict[str, Any]) -> Dict[str, Any]:
    token = _github_api_token()
    repo_slug = str(profile["slug"])
    default_branch = str(profile["default_branch"])
    workflow_runs_url = (
        f"https://api.github.com/repos/{repo_slug}/actions/runs"
        f"?branch={default_branch}&status=completed&per_page=50"
    )
    runs_payload = json.loads(_download_bytes(workflow_runs_url, token).decode("utf-8"))
    workflow_runs = runs_payload.get("workflow_runs", []) if isinstance(runs_payload, dict) else []
    run_id = _find_successful_run_id(workflow_runs, "Quality Zero Platform")
    if run_id is None:
        raise RuntimeError("Unable to find a successful Quality Zero Platform run on the default branch.")
    artifacts_url = f"https://api.github.com/repos/{repo_slug}/actions/runs/{run_id}/artifacts?per_page=100"
    artifacts_payload = json.loads(_download_bytes(artifacts_url, token).decode("utf-8"))
    artifacts = artifacts_payload.get("artifacts", []) if isinstance(artifacts_payload, dict) else []
    artifact = _find_artifact_by_name(artifacts, "coverage-artifacts")
    if artifact is None:
        raise RuntimeError("Unable to find coverage-artifacts on the baseline run.")
    archive_download_url = normalize_https_url(str(artifact["archive_download_url"]), allowed_hosts={"api.github.com"})
    archive = _download_bytes(archive_download_url, token)
    with zipfile.ZipFile(BytesIO(archive)) as handle:
        with handle.open("coverage-100/coverage.json") as stream:
            return json.loads(stream.read().decode("utf-8"))


def _render_non_regression_md(payload: dict) -> str:
    lines = [
        "# Coverage 100 Gate",
        "",
        f"- Status: `{payload['status']}`",
        "- Mode: `non_regression`",
        f"- Current combined coverage: `{payload['current_percent']:.2f}%`",
        f"- Baseline combined coverage: `{payload['baseline_percent']:.2f}%`",
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
        "",
        "## Findings",
    ]
    lines.extend([f"- {item}" for item in payload.get("findings", [])] or [NONE_BULLET])
    return "\n".join(lines) + "\n"


def _write_non_regression_report(current: Dict[str, Any], baseline: Dict[str, Any]) -> int:
    current_percent = _combined_coverage_percent(current)
    baseline_percent = _combined_coverage_percent(baseline)
    findings = []
    status = "pass"
    if current_percent < baseline_percent:
        status = "fail"
        findings.append(
            f"combined coverage regressed from {baseline_percent:.2f}% to {current_percent:.2f}%"
        )
    payload = {
        "status": status,
        "mode": "non_regression",
        "current_percent": current_percent,
        "baseline_percent": baseline_percent,
        "timestamp_utc": utc_timestamp(),
        "findings": findings,
    }
    return_code = write_report(
        payload,
        out_json=DEFAULT_COVERAGE_JSON,
        out_md=DEFAULT_COVERAGE_MD,
        default_json=DEFAULT_COVERAGE_JSON,
        default_md=DEFAULT_COVERAGE_MD,
        render_md=_render_non_regression_md,
    )
    if return_code != 0:
        return return_code
    return 0 if status == "pass" else 1


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
    if mode == "non_regression":
        current_payload = _collect_current_coverage_payload(coverage, repo_dir=repo_dir, platform_dir=platform_dir)
        baseline_payload = _load_baseline_coverage_payload(cast(Dict[str, Any], profile))
        return _write_non_regression_report(current_payload, baseline_payload)

    return _run_assert_coverage_100(coverage, repo_dir=repo_dir, platform_dir=platform_dir)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())  # pragma: no cover
