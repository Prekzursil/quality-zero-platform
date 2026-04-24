#!/usr/bin/env python3
"""Check quality secrets."""

from __future__ import absolute_import

import argparse
import os
import subprocess  # nosec B404 # noqa: S404 — indirect gh CLI via alerts opener
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality import alert_triggers, alerts
from scripts.quality.common import dedupe_strings, utc_timestamp, write_report

NONE_BULLET = "- None"


def _parse_args() -> argparse.Namespace:
    """Handle parse args."""
    parser = argparse.ArgumentParser(
        description="Validate required quality-gate secrets and variables."
    )
    parser.add_argument(
        "--required-secret",
        action="append",
        default=[],
        help="Required secret env var name",
    )
    parser.add_argument(
        "--conditional-secret",
        action="append",
        default=[],
        help="Conditional secret env var name",
    )
    parser.add_argument(
        "--required-var",
        action="append",
        default=[],
        help="Required variable env var name",
    )
    parser.add_argument(
        "--conditional-var",
        action="append",
        default=[],
        help="Conditional variable env var name",
    )
    parser.add_argument("--out-json", default="quality-secrets/secrets.json")
    parser.add_argument("--out-md", default="quality-secrets/secrets.md")
    parser.add_argument(
        "--open-alerts",
        action="store_true",
        help="Open alert:secret-missing issues on the platform repo for "
        "every missing required secret.",
    )
    parser.add_argument(
        "--dry-run-alerts",
        action="store_true",
        help="With --open-alerts, produce the would-be issues but do not "
        "invoke gh (safety net for testing).",
    )
    parser.add_argument(
        "--platform-slug",
        default="Prekzursil/quality-zero-platform",
        help="Platform repo to open alert issues on.",
    )
    parser.add_argument(
        "--target-repo-slug",
        default="",
        help="Repo the secrets are missing on (subject of the alert). "
        "Defaults to --platform-slug when empty.",
    )
    return parser.parse_args()


def _append_missing_section(lines: List[str], title: str, items: List[str]) -> None:
    """Handle append missing section."""
    lines.extend(["", title])
    lines.extend([f"- `{item}`" for item in items] or [NONE_BULLET])


def _render_md(payload: Mapping[str, Any]) -> str:
    """Handle render md."""
    lines = [
        "# Quality Secrets Preflight",
        "",
        f"- Status: `{payload['status']}`",
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
    ]
    _append_missing_section(
        lines, "## Missing secrets", payload.get("missing_secrets", [])
    )
    _append_missing_section(
        lines,
        "## Missing conditional secrets",
        payload.get("missing_conditional_secrets", []),
    )
    _append_missing_section(
        lines, "## Missing variables", payload.get("missing_vars", [])
    )
    _append_missing_section(
        lines,
        "## Missing conditional variables",
        payload.get("missing_conditional_vars", []),
    )
    return "\n".join(lines) + "\n"


def _missing_env_names(names: List[str]) -> List[str]:
    """Handle missing env names."""
    return [name for name in names if not str(os.environ.get(name, "")).strip()]


def _build_payload(
    *,
    required_secrets: List[str],
    conditional_secrets: List[str],
    required_vars: List[str],
    conditional_vars: List[str],
) -> Dict[str, Any]:
    """Handle build payload."""
    missing_secrets = _missing_env_names(required_secrets)
    missing_conditional_secrets = _missing_env_names(conditional_secrets)
    missing_vars = _missing_env_names(required_vars)
    missing_conditional_vars = _missing_env_names(conditional_vars)
    return {
        "status": "pass" if not missing_secrets and not missing_vars else "fail",
        "timestamp_utc": utc_timestamp(),
        "required_secrets": required_secrets,
        "conditional_secrets": conditional_secrets,
        "required_vars": required_vars,
        "conditional_vars": conditional_vars,
        "missing_secrets": missing_secrets,
        "missing_conditional_secrets": missing_conditional_secrets,
        "missing_vars": missing_vars,
        "missing_conditional_vars": missing_conditional_vars,
    }


def open_secret_missing_alerts(
    *,
    platform_slug: str,
    target_repo_slug: str,
    missing_secrets: Iterable[str],
    runner: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Open one ``alert:secret-missing`` issue per missing scanner secret.

    Thin composition of ``alert_triggers.detect_secret_missing`` and
    ``alerts.open_alert_issue``: the detector filters blank entries +
    builds pre-formatted bodies, the opener dedupes by canonical title
    so re-running this on the same set is a no-op after the first call.
    """
    triggers = alert_triggers.detect_secret_missing(
        slug=target_repo_slug, missing_secrets=missing_secrets,
    )
    results: List[Dict[str, Any]] = []
    for trigger in triggers:
        if dry_run:
            results.append({
                "number": 0,
                "title": alerts.alert_issue_title(
                    trigger.alert_type, trigger.subject,
                ),
                "created": False,
            })
            continue
        record = alerts.open_alert_issue(
            platform_slug,
            alert_type=trigger.alert_type,
            subject=trigger.subject,
            body=trigger.body,
            runner=runner,
        )
        results.append(dict(record))
    return results


def main() -> int:
    """Handle main."""
    args = _parse_args()
    required_secrets = dedupe_strings(args.required_secret or [])
    conditional_secrets = dedupe_strings(args.conditional_secret or [])
    required_vars = dedupe_strings(args.required_var or [])
    conditional_vars = dedupe_strings(args.conditional_var or [])
    payload = _build_payload(
        required_secrets=required_secrets,
        conditional_secrets=conditional_secrets,
        required_vars=required_vars,
        conditional_vars=conditional_vars,
    )
    return_code = write_report(
        payload,
        out_json=args.out_json,
        out_md=args.out_md,
        default_json="quality-secrets/secrets.json",
        default_md="quality-secrets/secrets.md",
        render_md=_render_md,
    )
    if return_code != 0:
        return return_code
    if args.open_alerts and payload["missing_secrets"]:
        open_secret_missing_alerts(
            platform_slug=args.platform_slug,
            target_repo_slug=args.target_repo_slug or args.platform_slug,
            missing_secrets=payload["missing_secrets"],
            dry_run=args.dry_run_alerts,
        )
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
