#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Mapping


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ensure Codex account auth exists on a trusted private runner.")
    parser.add_argument("--auth-file", default="~/.codex/auth.json")
    parser.add_argument("--bootstrap-env-var", default="CODEX_AUTH_JSON")
    parser.add_argument("--out-json", default="")
    return parser.parse_args()


def _write_payload(path: str, payload: Mapping[str, object]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve_auth_payload(args: argparse.Namespace) -> dict[str, str]:
    auth_path = Path(args.auth_file).expanduser()
    auth_path.parent.mkdir(parents=True, exist_ok=True)

    if auth_path.is_file() and auth_path.stat().st_size > 0:
        return {"auth_file": str(auth_path), "source": "existing"}

    bootstrap_value = os.environ.get(args.bootstrap_env_var, "").strip()
    if bootstrap_value:
        try:
            parsed = json.loads(bootstrap_value)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{args.bootstrap_env_var} is not valid JSON: {exc}") from exc
        auth_path.write_text(json.dumps(parsed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"auth_file": str(auth_path), "source": args.bootstrap_env_var}

    raise SystemExit(
        "Codex account auth is missing. Seed the trusted private runner once with `codex login` "
        f"or provide {args.bootstrap_env_var} as a repository secret for bootstrap."
    )


def main() -> int:
    args = _parse_args()
    payload = _resolve_auth_payload(args)
    _write_payload(args.out_json, payload)
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
