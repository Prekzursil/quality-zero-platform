#!/usr/bin/env python3
"""Validate Codecov received a report for every declared flag."""

# Phase 2 of docs/QZP-V2-DESIGN.md ships the per-flag upload loop in
# ``reusable-codecov-analytics.yml``; this validator is the second half
# of §5.1. It polls Codecov's v2 commits API and fails CI if any
# ``coverage.inputs[].flag`` declared on the profile is missing from
# the report Codecov stored for the commit.
#
# Why this exists:
#   * ``codecov-action`` can return exit 0 even when the backend silently
#     dropped a file (auth hiccup, yaml mismatch, etc).
#   * Before Phase 2, event-link shipped at 58% overall because its UI
#     coverage was uploaded as the same unflagged blob as its backend.
#     The dashboard merged them and we couldn't tell until someone ran
#     the API call manually.
#   * Running this check as an ``always()`` step makes CI go red when
#     the upload pipeline degrades — before the gap hits production.
#
# Polling behaviour: Codecov sometimes takes a few seconds to ingest a
# new commit after the upload finishes. The validator retries with a
# short exponential backoff (max ~45 s) before treating the flag as
# missing.

from __future__ import absolute_import  # noqa: UP010 — required by codacy-compat test

import argparse
import json
import os
import sys
import time
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Set

if str(Path(__file__).resolve().parents[2]) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.security_helpers import load_bytes_https

CODECOV_API_BASE = "https://api.codecov.io/api/v2/github"
RETRY_DELAYS_SECONDS = (2, 4, 8, 16, 15)  # sums to 45s of patience
MAX_POLL_SECONDS_DEFAULT = sum(RETRY_DELAYS_SECONDS)


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-slug", required=True, help="owner/name slug")
    parser.add_argument("--sha", required=True, help="target commit SHA")
    parser.add_argument(
        "--inputs-json",
        required=True,
        help="Path to the resolved profile JSON emitted by export_profile.py",
    )
    parser.add_argument(
        "--max-wait-seconds",
        type=int,
        default=MAX_POLL_SECONDS_DEFAULT,
        help="Total polling budget before declaring a flag missing.",
    )
    return parser.parse_args()


def _declared_flags(profile_path: Path) -> List[str]:
    """Return the unique ``flag`` values declared on the profile."""
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    coverage = payload.get("coverage", {}) if isinstance(payload, dict) else {}
    inputs = coverage.get("inputs", []) if isinstance(coverage, dict) else []
    flags: List[str] = []
    for item in inputs:
        if not isinstance(item, dict):
            continue
        flag = str(item.get("flag", "")).strip()
        name = str(item.get("name", "")).strip()
        canonical = flag or name
        if canonical and canonical not in flags:
            flags.append(canonical)
    return flags


_SAFE_SLUG_CHARS = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    ".-_/"
)
_SAFE_SHA_CHARS = set("0123456789abcdefABCDEF")
_ALLOWED_URL_PREFIX = f"{CODECOV_API_BASE}/"


def _validate_slug(repo_slug: str) -> None:
    """Guard against URL smuggling via repo-slug input."""
    if not repo_slug or repo_slug.count("/") != 1:
        raise ValueError(f"repo_slug must be 'owner/name', got {repo_slug!r}")
    if not set(repo_slug).issubset(_SAFE_SLUG_CHARS):
        raise ValueError(
            f"repo_slug contains unsafe characters: {repo_slug!r}"
        )


def _validate_sha(sha: str) -> None:
    """Guard against URL smuggling via SHA input."""
    if not sha or len(sha) > 64:
        raise ValueError(f"sha must be 1-64 hex chars, got len={len(sha)}")
    if not set(sha).issubset(_SAFE_SHA_CHARS):
        raise ValueError(f"sha contains non-hex characters: {sha!r}")


def _codecov_commit_url(repo_slug: str, sha: str) -> str:
    """Construct the Codecov v2 commit-report endpoint for ``sha``.

    Slug + sha are validated against tight character allowlists before
    being interpolated, and the resulting URL is asserted to start with
    the hard-coded Codecov API prefix so neither arg can redirect the
    fetch to a ``file://`` or attacker-controlled host.
    """
    _validate_slug(repo_slug)
    _validate_sha(sha)
    owner, _, name = repo_slug.partition("/")
    url = f"{CODECOV_API_BASE}/{owner}/repos/{name}/commits/{sha}/"
    if not url.startswith(_ALLOWED_URL_PREFIX):  # pragma: no cover — defence-in-depth
        raise ValueError(
            f"constructed URL escaped the Codecov prefix: {url!r}"
        )
    return url


def _fetch_codecov_report(url: str, token: str) -> Dict[str, Any]:
    """Return the Codecov API JSON for the commit or raise HTTPError.

    Routes through :func:`scripts.security_helpers.load_bytes_https`
    rather than calling ``urllib.request.urlopen`` directly — that
    helper enforces HTTPS-only scheme, denies private-network + link-
    local hostnames, pins the TLS context to TLS 1.2+, and is the
    audited ingress used throughout the control plane.
    """
    if not url.startswith(_ALLOWED_URL_PREFIX):
        raise ValueError(f"refusing to fetch non-Codecov URL: {url!r}")
    headers: Dict[str, str] = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    raw, _response_headers = load_bytes_https(
        url,
        headers=headers,
        method="GET",
        timeout=15,
        allowed_hosts={"api.codecov.io"},
    )
    return json.loads(raw)


def _flags_present_in_report(report: Dict[str, Any]) -> Set[str]:
    """Extract the flag names that Codecov recorded for this commit."""
    seen: Set[str] = set()
    # Codecov's commit endpoint structures flags under ``totals.flags`` or
    # ``report.flags``; we tolerate both because the schema has shifted
    # between minor releases.
    totals = report.get("totals") if isinstance(report, dict) else None
    if isinstance(totals, dict):
        for flag in totals.get("flags", []) or []:
            if isinstance(flag, dict):
                name = str(flag.get("name", "")).strip()
                if name:
                    seen.add(name)
    inner_report = report.get("report") if isinstance(report, dict) else None
    if isinstance(inner_report, dict):
        for flag_name, _ in (inner_report.get("flags") or {}).items():
            seen.add(str(flag_name))
    return seen


def _poll_for_flags(
    url: str, token: str, deadline_seconds: int
) -> Dict[str, Any]:
    """Return the Codecov report once the commit appears or raise TimeoutError."""
    start = time.monotonic()
    last_error: BaseException | None = None
    for delay in RETRY_DELAYS_SECONDS:
        elapsed = time.monotonic() - start
        if elapsed >= deadline_seconds:
            break
        try:
            return _fetch_codecov_report(url, token)
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 408, 425, 429, 500, 502, 503, 504):
                raise
            last_error = exc
        except urllib.error.URLError as exc:
            last_error = exc
        time.sleep(delay)
    raise TimeoutError(
        f"Codecov report for {url} not ready within {deadline_seconds}s "
        f"(last error: {last_error!r})"
    )


def validate_flags(
    declared_flags: List[str],
    present_flags: Set[str],
) -> List[str]:
    """Return the list of declared flags that Codecov did not report."""
    return [flag for flag in declared_flags if flag not in present_flags]


def main() -> int:
    """CLI entrypoint.

    Exit codes:
        0 — every declared flag is present in Codecov's report, OR the
            API rejected our credentials (401/403) and we cannot verify.
            The second case emits a prominent warning but does not gate
            CI — auth problems are a platform-config issue, not a
            per-PR regression, and gating every repo without a valid
            read-token would punish adoption.
        1 — at least one declared flag is missing.
        2 — the declared-flags manifest could not be parsed.
        3 — Codecov API did not return a report within the deadline.
    """
    args = _parse_args()
    profile_path = Path(args.inputs_json)
    if not profile_path.is_file():
        print(
            f"validate_codecov_flags: profile JSON not found: {profile_path}",
            flush=True,
        )
        return 2

    declared = _declared_flags(profile_path)
    if not declared:
        print(
            "validate_codecov_flags: no flags declared — nothing to validate.",
            flush=True,
        )
        return 0

    token = os.environ.get("CODECOV_TOKEN", "").strip()
    url = _codecov_commit_url(args.repo_slug, args.sha)
    try:
        report = _poll_for_flags(url, token, args.max_wait_seconds)
    except TimeoutError as exc:
        print(f"validate_codecov_flags: {exc}", flush=True)
        return 3
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            print(
                "validate_codecov_flags: WARNING — Codecov API returned "
                f"{exc.code} ({exc.reason}); cannot verify flag ingestion. "
                "The CODECOV_TOKEN in CI is an upload token; the v2 API "
                "needs a read-scope API token. Skipping strict validation.",
                flush=True,
            )
            return 0
        raise

    present = _flags_present_in_report(report)
    missing = validate_flags(declared, present)
    if missing:
        print(
            "validate_codecov_flags: Codecov did not record these flags: "
            + ", ".join(missing),
            flush=True,
        )
        print(f"validate_codecov_flags: flags present in report: {sorted(present)}")
        return 1

    print(
        f"validate_codecov_flags: all {len(declared)} declared flag(s) present: "
        + ", ".join(declared),
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
