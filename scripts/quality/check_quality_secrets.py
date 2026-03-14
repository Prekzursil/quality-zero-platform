#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.common import dedupe_strings, utc_timestamp, write_report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate required quality-gate secrets and variables.")
    parser.add_argument("--required-secret", action="append", default=[], help="Required secret env var name")
    parser.add_argument("--conditional-secret", action="append", default=[], help="Conditional secret env var name")
    parser.add_argument("--required-var", action="append", default=[], help="Required variable env var name")
    parser.add_argument("--conditional-var", action="append", default=[], help="Conditional variable env var name")
    parser.add_argument("--out-json", default="quality-secrets/secrets.json")
    parser.add_argument("--out-md", default="quality-secrets/secrets.md")
    return parser.parse_args()


def _render_md(payload: dict) -> str:
    lines = [
        "# Quality Secrets Preflight",
        "",
        f"- Status: `{payload['status']}`",
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
        "",
        "## Missing secrets",
    ]
    lines.extend([f"- `{item}`" for item in payload.get("missing_secrets", [])] or ["- None"])
    lines.extend(["", "## Missing conditional secrets"])
    lines.extend([f"- `{item}`" for item in payload.get("missing_conditional_secrets", [])] or ["- None"])
    lines.extend(["", "## Missing variables"])
    lines.extend([f"- `{item}`" for item in payload.get("missing_vars", [])] or ["- None"])
    lines.extend(["", "## Missing conditional variables"])
    lines.extend([f"- `{item}`" for item in payload.get("missing_conditional_vars", [])] or ["- None"])
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _parse_args()
    required_secrets = dedupe_strings(args.required_secret or [])
    conditional_secrets = dedupe_strings(args.conditional_secret or [])
    required_vars = dedupe_strings(args.required_var or [])
    conditional_vars = dedupe_strings(args.conditional_var or [])
    missing_secrets = [name for name in required_secrets if not str(os.environ.get(name, "")).strip()]
    missing_conditional_secrets = [name for name in conditional_secrets if not str(os.environ.get(name, "")).strip()]
    missing_vars = [name for name in required_vars if not str(os.environ.get(name, "")).strip()]
    missing_conditional_vars = [name for name in conditional_vars if not str(os.environ.get(name, "")).strip()]
    payload = {
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
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
