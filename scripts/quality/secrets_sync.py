#!/usr/bin/env python3
"""Phase 5 secrets-sync primitive — propagate a secret + audit the action.

Called by ``reusable-secrets-sync.yml`` to:

1. Write a secret to every ``target_slugs`` repo via ``gh secret set``.
2. Append one JSONL record per target to the audit log (secret VALUE
   is never logged — only the name + destination slug + result).

The function is structured with dependency injection (``runner``) so
the full flow is unit-testable without touching gh or the network.

Per ``docs/QZP-V2-DESIGN.md`` §9:

    Repo-level secret sync workflow uses a fine-grained PAT scoped only
    to ``repo.secrets:write``; every sync write appends to
    ``audit/secrets-sync.jsonl`` with no secret values logged.
"""

from __future__ import absolute_import

import datetime as dt
import json
import subprocess  # nosec B404 # noqa: S404 — gh CLI wrapper; args are controlled
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()


ProcessRunner = Callable[..., "subprocess.CompletedProcess[str]"]


def _utc_now_iso() -> str:
    """Return the current UTC time as ``YYYY-MM-DDTHH:MM:SSZ``."""
    return dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gh_secret_set(
    *, secret_name: str, secret_value: str, target_slug: str,
    runner: ProcessRunner,
) -> "subprocess.CompletedProcess[str]":
    """Invoke ``gh secret set`` on one target repo."""
    return runner(
        [
            "gh", "secret", "set", secret_name,
            "--repo", target_slug,
            "--body", secret_value,
        ],
        capture_output=True,
        text=True,
        check=False,
    )  # nosec B603 — gh args are controlled by call site


def sync_secret(
    *,
    secret_name: str,
    secret_value: str,
    target_slugs: Iterable[str],
    runner: ProcessRunner = subprocess.run,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Propagate ``secret_name`` to each ``target_slug``; return audit records.

    Returns a list of records with ``target_slug`` / ``secret_name`` /
    ``status`` / ``timestamp_utc`` — the secret VALUE is never included.
    """
    records: List[Dict[str, Any]] = []
    for slug in target_slugs:
        target = str(slug).strip()
        if not target:
            continue
        if dry_run:
            status = "dry-run"
        else:
            completed = _gh_secret_set(
                secret_name=secret_name,
                secret_value=secret_value,
                target_slug=target,
                runner=runner,
            )
            status = "synced" if completed.returncode == 0 else "failed"
        records.append({
            "secret_name": secret_name,
            "status": status,
            "target_slug": target,
            "timestamp_utc": _utc_now_iso(),
        })
    return records


def append_audit_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    """Append ``records`` to ``path`` as one-line JSONL with sort_keys=True."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
            )


if __name__ == "__main__":  # pragma: no cover — ad-hoc CLI
    import argparse
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-name", required=True)
    parser.add_argument(
        "--secret-env",
        required=True,
        help="Env var holding the secret value (never a CLI arg).",
    )
    parser.add_argument(
        "--targets", required=True,
        help="Comma-separated list of owner/name slugs.",
    )
    parser.add_argument("--audit-log", default="audit/secrets-sync.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    _args = parser.parse_args()

    _value = os.environ.get(_args.secret_env, "").strip()
    if not _value and not _args.dry_run:
        print(
            f"::error::secret value env var {_args.secret_env!r} is empty",
            file=sys.stderr, flush=True,
        )
        raise SystemExit(2)
    _targets = [s.strip() for s in _args.targets.split(",") if s.strip()]
    _records = sync_secret(
        secret_name=_args.secret_name,
        secret_value=_value,
        target_slugs=_targets,
        dry_run=_args.dry_run,
    )
    append_audit_jsonl(Path(_args.audit_log), _records)
    print(json.dumps(_records, indent=2, sort_keys=True))
