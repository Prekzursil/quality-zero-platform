#!/usr/bin/env python3
"""Check sentry zero."""

from __future__ import absolute_import

import argparse
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple
from urllib.error import HTTPError

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import dedupe_strings, utc_timestamp, write_report
from scripts.security_helpers import load_json_https

SENTRY_API_BASE = "https://sentry.io/api/0"


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the Sentry zero gate."""
    parser = argparse.ArgumentParser(
        description=(
            "Assert Sentry has zero unresolved issues for configured projects."
        )
    )
    parser.add_argument("--org", default="")
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument("--token", default="")
    parser.add_argument("--out-json", default="sentry-zero/sentry.json")
    parser.add_argument("--out-md", default="sentry-zero/sentry.md")
    return parser.parse_args()


def _request_json(url: str, token: str) -> Tuple[Any, Dict[str, str]]:
    """Load a Sentry API response using the shared HTTPS helper."""
    return load_json_https(
        url,
        allowed_host_suffixes={"sentry.io"},
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "quality-zero-platform",
        },
    )


def _hits_from_headers(headers: Dict[str, str]) -> int | None:
    """Read Sentry's unresolved issue count from response headers when present."""
    raw = headers.get("x-hits")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _collect_projects(args_projects: List[str]) -> List[str]:
    """Merge CLI and environment-provided project slugs into a deduped list."""
    inputs = list(args_projects)
    for env_name in ("SENTRY_PROJECT", "SENTRY_PROJECT_BACKEND", "SENTRY_PROJECT_WEB"):
        value = str(os.environ.get(env_name, "")).strip()
        if value:
            inputs.extend(value.replace(";", ",").replace("\n", ",").split(","))
    return dedupe_strings(inputs)


def _render_md(payload: Mapping[str, Any]) -> str:
    """Render a human-readable Sentry gate report."""
    lines = [
        "# Sentry Zero Gate",
        "",
        f"- Status: `{payload['status']}`",
        f"- Org: `{payload.get('org')}`",
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
        "",
        "## Project results",
    ]
    if payload.get("projects"):
        for item in payload["projects"]:
            state = str(item.get("state") or "ok")
            state_suffix = "" if state == "ok" else f" state=`{state}`"
            lines.append(
                f"- `{item['project']}` unresolved=`{item['unresolved']}`"
                f"{state_suffix}"
            )
    else:
        lines.append("- None")
    lines.extend(["", "## Findings"])
    lines.extend([f"- {item}" for item in payload.get("findings", [])] or ["- None"])
    return "\n".join(lines) + "\n"


def _issues_url(org: str, project_slug: str) -> str:
    """Build the unresolved-issues endpoint for a Sentry project."""
    org_slug = urllib.parse.quote(org, safe="")
    project_param = urllib.parse.quote(project_slug, safe="")
    query = urllib.parse.urlencode(
        [
            ("query", "is:unresolved"),
            ("limit", "1"),
            ("project", project_param),
        ]
    )
    return f"{SENTRY_API_BASE}/projects/{org_slug}/{project_param}/issues/?{query}"


def _validate_sentry_inputs(token: str, org: str, projects: List[str]) -> List[str]:
    """Return any input validation findings before calling the Sentry API."""
    findings: List[str] = []
    if not token:
        findings.append("SENTRY_AUTH_TOKEN is missing.")
    if not org:
        findings.append("SENTRY_ORG is missing.")
    if not projects:
        findings.append("No Sentry projects configured.")
    return findings


def _collect_project_results(
    org: str,
    projects: List[str],
    token: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Load unresolved-issue counts for each configured Sentry project."""
    findings: List[str] = []
    project_results: List[Dict[str, Any]] = []
    for project in projects:
        try:
            payload, headers = _request_json(_issues_url(org, project), token)
        except HTTPError as exc:
            if exc.code == 404:
                project_results.append(
                    {
                        "project": project,
                        "unresolved": 0,
                        "state": "not_found",
                    }
                )
                continue
            raise
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected Sentry issues response payload")
        unresolved = _hits_from_headers(headers)
        if unresolved is None:
            unresolved = len(payload)
        if unresolved != 0:
            findings.append(
                f"Sentry project {project} has {unresolved} unresolved issues "
                "(expected 0)."
            )
        project_results.append(
            {
                "project": project,
                "unresolved": unresolved,
                "state": "ok",
            }
        )
    return project_results, findings


def main() -> int:
    """Run the Sentry zero gate and write its report payload."""
    args = _parse_args()
    token = (args.token or os.environ.get("SENTRY_AUTH_TOKEN", "")).strip()
    org = (args.org or os.environ.get("SENTRY_ORG", "")).strip()
    projects = _collect_projects(args.project)
    findings = _validate_sentry_inputs(token, org, projects)
    project_results: List[Dict[str, Any]] = []

    status = "fail"
    if not findings:
        try:
            project_results, findings = _collect_project_results(org, projects, token)
            status = "pass" if not findings else "fail"
        except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
            findings.append(f"Sentry API request failed: {exc}")

    payload = {
        "status": status,
        "org": org,
        "projects": project_results,
        "timestamp_utc": utc_timestamp(),
        "findings": findings,
    }
    return_code = write_report(
        payload,
        out_json=args.out_json,
        out_md=args.out_md,
        default_json="sentry-zero/sentry.json",
        default_md="sentry-zero/sentry.md",
        render_md=_render_md,
    )
    if return_code != 0:
        return return_code
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
