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
from typing import Any, Callable, Dict, Iterable, List, Optional


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()


ProcessRunner = Callable[..., "subprocess.CompletedProcess[str]"]
SecretSetter = Callable[[str, str], "subprocess.CompletedProcess[str]"]


def _utc_now_iso() -> str:
    """Return the current UTC time as ``YYYY-MM-DDTHH:MM:SSZ``."""
    return dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_gh_secret_setter(
    secret_value: str,
    *, runner: ProcessRunner = subprocess.run,
) -> SecretSetter:
    """Build a ``SecretSetter`` closure capturing ``secret_value``.

    Callers construct this once from a trusted source (env var or
    ``secrets:`` context) and hand it to ``sync_secret``. The rest of
    the sync flow never sees the secret value — it only sees the
    opaque setter callable. This keeps ``sync_secret``'s scope free
    of tainted data for CodeQL's clear-text-sensitive-data check.
    """
    def _set(
        secret_name: str, target_slug: str,
    ) -> "subprocess.CompletedProcess[str]":
        return runner(
            [
                "gh", "secret", "set", secret_name,
                "--repo", target_slug,
                "--body", secret_value,
            ],
            capture_output=True, text=True, check=False,
        )  # nosec B603 — gh args are controlled by call site
    return _set


def sync_secret(
    *,
    secret_name: str,
    target_slugs: Iterable[str],
    secret_setter: Optional[SecretSetter] = None,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Propagate ``secret_name`` to each ``target_slug``; return audit records.

    ``secret_setter`` is the ONLY channel the secret VALUE takes — it
    is captured inside the closure produced by
    ``make_gh_secret_setter`` and never enters this function's scope,
    which keeps audit-record construction entirely free of tainted
    data from the caller's perspective.

    Returns a list of records with ``target_slug`` / ``secret_name`` /
    ``status`` / ``timestamp_utc`` — the secret VALUE is never logged.
    """
    records: List[Dict[str, Any]] = []
    for slug in target_slugs:
        target = str(slug).strip()
        if not target:
            continue
        if dry_run or secret_setter is None:
            status = "dry-run"
        else:
            completed = secret_setter(secret_name, target)
            status = "synced" if completed.returncode == 0 else "failed"
        records.append({
            "secret_name": secret_name,
            "status": status,
            "target_slug": target,
            "timestamp_utc": _utc_now_iso(),
        })
    return records


_AUDIT_FIELDS: tuple = ("secret_name", "status", "target_slug", "timestamp_utc")


def _sanitise_audit_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Re-project a record onto the known-safe ``_AUDIT_FIELDS`` set only.

    This is an explicit sanitiser: only the four whitelisted keys survive,
    and each value is coerced to ``str``. Static analysers that track
    taint through arbitrary dict flows break at this boundary because
    every field is re-materialised from a closed set of safe keys.
    """
    return {field: str(record.get(field, "")) for field in _AUDIT_FIELDS}


def append_audit_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    """Append ``records`` to ``path`` as one-line JSONL with sort_keys=True.

    Each record is sanitised to the whitelisted ``_AUDIT_FIELDS`` set
    before serialisation — anything else in the mapping (including any
    hypothetical ``secret_value`` key a misuse might introduce) is
    dropped at this boundary, not merely relied upon by convention.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for record in records:
            safe = _sanitise_audit_record(record)
            fh.write(
                json.dumps(safe, sort_keys=True, separators=(",", ":")) + "\n",
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
        # Do NOT mention the env-var NAME in user-facing output either
        # (static analysers flag even the env-var name as "secret-adjacent").
        sys.stderr.write("::error::secret value env var is empty\n")
        raise SystemExit(2)
    _targets = [s.strip() for s in _args.targets.split(",") if s.strip()]
    _setter = (
        None if _args.dry_run
        else make_gh_secret_setter(_value)
    )
    _records = sync_secret(
        secret_name=_args.secret_name,
        target_slugs=_targets,
        secret_setter=_setter,
        dry_run=_args.dry_run,
    )
    append_audit_jsonl(Path(_args.audit_log), _records)
    # Summary only — never print records themselves (static analysers
    # treat record dicts as secret-tainted via the closure capture
    # chain, even though the sanitiser in append_audit_jsonl drops
    # any non-whitelisted fields).
    _synced = sum(1 for r in _records if r.get("status") == "synced")
    _failed = sum(1 for r in _records if r.get("status") == "failed")
    sys.stdout.write(
        f"secrets-sync: {len(_records)} targets, "
        f"{_synced} synced, {_failed} failed\n",
    )
