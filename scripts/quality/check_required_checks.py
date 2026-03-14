#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import utc_timestamp, write_report
from scripts.security_helpers import load_json_https


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wait for required GitHub contexts and assert they are successful.")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--sha", required=True, help="commit SHA")
    parser.add_argument("--required-context", action="append", default=[], help="Required context name")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--poll-seconds", type=int, default=20)
    parser.add_argument("--out-json", default="quality-zero-gate/required-checks.json")
    parser.add_argument("--out-md", default="quality-zero-gate/required-checks.md")
    return parser.parse_args()


def _api_get(repo: str, path: str, token: str) -> dict[str, Any]:
    payload, _ = load_json_https(
        f"https://api.github.com/repos/{repo}/{path.lstrip('/')}",
        allowed_hosts={"api.github.com"},
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "quality-zero-platform",
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected GitHub API response payload")
    return payload


def _collect_contexts(check_runs_payload: dict[str, Any], status_payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    contexts: dict[str, dict[str, str]] = {}
    for run in check_runs_payload.get("check_runs", []) or []:
        name = str(run.get("name") or "").strip()
        if not name:
            continue
        contexts[name] = {
            "state": str(run.get("status") or ""),
            "conclusion": str(run.get("conclusion") or ""),
            "source": "check_run",
        }

    for status in status_payload.get("statuses", []) or []:
        name = str(status.get("context") or "").strip()
        if not name:
            continue
        contexts[name] = {
            "state": str(status.get("state") or ""),
            "conclusion": str(status.get("state") or ""),
            "source": "status",
        }
    return contexts


def _evaluate(required: list[str], contexts: dict[str, dict[str, str]]) -> tuple[str, list[str], list[str]]:
    missing: list[str] = []
    failed: list[str] = []
    for context in required:
        observed = contexts.get(context)
        if not observed:
            missing.append(context)
            continue
        if observed["source"] == "check_run":
            if observed["state"] != "completed":
                failed.append(f"{context}: status={observed['state']}")
            elif observed["conclusion"] != "success":
                failed.append(f"{context}: conclusion={observed['conclusion']}")
        elif observed["conclusion"] != "success":
            failed.append(f"{context}: state={observed['conclusion']}")
    return ("pass" if not missing and not failed else "fail", missing, failed)


def _render_md(payload: dict[str, Any]) -> str:
    lines = [
        "# Quality Zero Gate - Required Contexts",
        "",
        f"- Status: `{payload['status']}`",
        f"- Repo/SHA: `{payload['repo']}@{payload['sha']}`",
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
        "",
        "## Missing contexts",
    ]
    lines.extend([f"- `{item}`" for item in payload.get("missing", [])] or ["- None"])
    lines.extend(["", "## Failed contexts"])
    lines.extend([f"- {item}" for item in payload.get("failed", [])] or ["- None"])
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _parse_args()
    token = (os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")).strip()
    required = [item.strip() for item in args.required_context if item.strip()]
    if not required:
        raise SystemExit("At least one --required-context is required")
    if not token:
        raise SystemExit("GITHUB_TOKEN or GH_TOKEN is required")

    deadline = time.time() + max(args.timeout_seconds, 1)
    final_payload: dict[str, Any] | None = None
    while time.time() <= deadline:
        check_runs = _api_get(args.repo, f"commits/{args.sha}/check-runs?per_page=100", token)
        statuses = _api_get(args.repo, f"commits/{args.sha}/status", token)
        contexts = _collect_contexts(check_runs, statuses)
        status, missing, failed = _evaluate(required, contexts)
        final_payload = {
            "status": status,
            "repo": args.repo,
            "sha": args.sha,
            "required": required,
            "missing": missing,
            "failed": failed,
            "contexts": contexts,
            "timestamp_utc": utc_timestamp(),
        }
        if status == "pass":
            break
        in_progress = any(v.get("state") != "completed" for v in contexts.values() if v.get("source") == "check_run")
        if not missing and not in_progress:
            break
        time.sleep(max(args.poll_seconds, 1))

    if final_payload is None:
        raise SystemExit("No payload collected")

    return_code = write_report(
        final_payload,
        out_json=args.out_json,
        out_md=args.out_md,
        default_json="quality-zero-gate/required-checks.json",
        default_md="quality-zero-gate/required-checks.md",
        render_md=_render_md,
    )
    if return_code != 0:
        return return_code
    return 0 if final_payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
