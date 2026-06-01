#!/usr/bin/env python3
"""TG-2 — token-rotation preflight.

For every scanner that is **block-severity in the resolved profile**, classify
its read-capability *truthfully*:

* ``ok`` — an authenticated probe returned 2xx (the token is live).
* ``secret_missing`` — the required secret is absent from the environment.
* ``unreadable`` — the secret is present but REJECTED (HTTP 401/403) or the
  endpoint is unreachable / allowlist-blocked. This is the rotated-token case
  the campaign's master blocker hides, so it dominates the exit code.

The exit precedence (``unreadable`` → 2 dominates ``secret_missing`` → 1
dominates all-``ok`` → 0) is fail-closed and deterministic.

**No silent skips.** A block-severity scanner that is in NEITHER
``PROVIDER_PROBES`` NOR ``EXEMPT_BLOCK_SCANNERS`` raises
``UnclassifiedScannerError`` — that is the truth-model's north-star #2.

The three auth-probed secrets (``SONAR_TOKEN``, ``CODACY_API_TOKEN``,
``SENTRY_AUTH_TOKEN``) live only in CI, so the live exit-0 acceptance is
verified on the PR, not locally; the unit tests inject a mocked ``loader``.

Token non-leak (A.CB-8 clause 4): ``diagnostic`` carries only
``f"{provider}: HTTP {status}"`` / ``f"{provider}: unreachable"`` — never the
token value, never the URL or query string.
"""

from __future__ import absolute_import

import argparse
import os
import ssl
import sys
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence

if str(Path(__file__).resolve().parents[3]) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality import alerts, control_plane
from scripts.security_helpers import load_json_https

Outcome = Literal["ok", "secret_missing", "unreadable"]

# A loader compatible with ``scripts.security_helpers.load_json_https``: it
# RAISES ``urllib.error.HTTPError`` on status >= 400 and returns a
# ``(payload, headers)`` tuple on 2xx. Tests inject a double to stay offline.
Loader = Callable[..., Any]
ProfileLoader = Callable[[str], Mapping[str, Any]]
AlertOpener = Callable[..., Mapping[str, Any]]

DEFAULT_PLATFORM_SLUG = "Prekzursil/quality-zero-platform"
_USER_AGENT = "quality-zero-platform"

EXIT_OK = 0
EXIT_SECRET_MISSING = 1
EXIT_UNREADABLE = 2

# The exception families that mean "token present but the read failed". Split
# from ``HTTPError`` so the diagnostic can carry the HTTP status only for the
# rejected case; everything else is ``unreachable`` (``http_status`` None).
# ``ValueError`` covers ``normalize_https_url`` rejecting a non-allowlisted /
# non-HTTPS URL — an SSRF-guard reject is "unreadable", never a silent pass.
_UNREACHABLE_ERRORS = (
    urllib.error.URLError,
    OSError,
    TimeoutError,
    ValueError,
    ssl.SSLError,
)


def _accept_any(_payload: Any) -> bool:
    """Default body predicate: any 2xx payload means the token is live."""
    return True


def _accept_sonar_valid(payload: Any) -> bool:
    """SonarCloud predicate: ``api/authentication/validate`` 200s for any token.

    The endpoint answers HTTP 200 regardless of token liveness; the body
    carries the verdict (``{"valid": true|false}``). A rotated token yields
    ``{"valid": false}`` with HTTP 200, so trusting 2xx alone would mark it
    ``ok`` — the exact false-negative TG-2 exists to catch.
    """
    return isinstance(payload, Mapping) and payload.get("valid") is True


@dataclass(frozen=True)
class ProbeSpec:
    """One auth-probe: the secret, the request, the SSRF allowlist, the auth.

    The request host and the SSRF ``allowed_host_suffix`` are SEPARATE fields:
    Codacy's API lives on ``app.codacy.com`` while its allowlist suffix is
    ``codacy.com``. ``allowed_host_suffix`` is MANDATORY and non-empty — an
    empty suffix silently disables ``normalize_https_url``'s SSRF guard.

    ``request_url`` may be empty when the endpoint is configured at runtime
    (DeepScan mirrors ``check_deepscan_zero.py``'s ``DEEPSCAN_OPEN_ISSUES_URL``);
    ``url_env`` then names the env var that supplies it. ``auth_header`` /
    ``auth_format`` carry the per-provider scheme (Sonar/Sentry/DeepScan use
    ``Authorization: Bearer <token>``; Codacy uses ``api-token: <token>``).
    ``accepts`` is the body predicate (default: any 2xx is live; Sonar checks
    ``valid``).
    """

    secret_env: str
    request_url: str
    allowed_host_suffix: str
    auth_header: str
    auth_format: str
    accepts: Callable[[Any], bool] = _accept_any
    url_env: str = ""


@dataclass(frozen=True)
class ProbeResult:
    """The truthful classification of one block-severity scanner."""

    provider: str
    outcome: Outcome
    http_status: Optional[int]
    diagnostic: str


class UnclassifiedScannerError(RuntimeError):
    """A block-severity scanner is in NEITHER the probe table NOR the exempt set.

    Raised loudly (never a silent skip) so a newly-added block scanner can't
    slip through the preflight unnoticed — truth-model north-star #2.
    """


# AUTH-PROBED providers (read-capable tokens). The 8 names collapse onto the 3
# distinct read tokens: the normalized profile expands each logical provider
# into per-facet scanner names (``codacy_issues`` etc.) + carries v1/v2
# aliases (``sonar``/``sonarcloud``, ``codacy``/``codacy_*``). Per-facet
# probing is incoherent — the auth endpoints are provider-level — so every
# facet of a provider shares one spec. Endpoints/auth schemes are confirmed
# from the providers' public API docs; CI verifies live exit-0 (and the
# deliberate-bad-token → exit-2 case) against the real tokens.
_SONAR = ProbeSpec(
    secret_env="SONAR_TOKEN",  # noqa: S106  # nosec — env-var NAME, not a secret
    # SonarCloud web API: ``GET api/authentication/validate`` returns HTTP 200
    # for ANY token; the body's ``valid`` flag is the verdict (see _accept_sonar_valid).
    request_url="https://sonarcloud.io/api/authentication/validate",
    allowed_host_suffix="sonarcloud.io",
    auth_header="Authorization",
    auth_format="Bearer {token}",
    accepts=_accept_sonar_valid,
)
_CODACY = ProbeSpec(
    secret_env="CODACY_API_TOKEN",  # noqa: S106  # nosec — env-var NAME, not a secret
    # Codacy API v3 lives on app.codacy.com and authenticates with the
    # ``api-token`` header (NOT Bearer); a rotated token → 401 (load_json_https
    # raises HTTPError), so any 2xx is live. ``/api/v3/user/organizations/gh``
    # is the doc-attested account-token whoami route (the bare ``/api/v3/user``
    # is not a documented endpoint). Docs: docs.codacy.com/codacy-api.
    request_url="https://app.codacy.com/api/v3/user/organizations/gh",
    allowed_host_suffix="codacy.com",
    auth_header="api-token",
    auth_format="{token}",
)
_SENTRY = ProbeSpec(
    secret_env="SENTRY_AUTH_TOKEN",  # noqa: S106  # nosec — env-var NAME, not a secret
    # Sentry web API authenticates with ``Authorization: Bearer``; the
    # authenticated ``/api/0/organizations/`` endpoint 401s on a dead token, so
    # any 2xx is live. Docs: docs.sentry.io/api/auth. (The bare ``/api/0/``
    # index does not require auth, so it cannot detect a rotated token.)
    request_url="https://sentry.io/api/0/organizations/",
    allowed_host_suffix="sentry.io",
    auth_header="Authorization",
    auth_format="Bearer {token}",
)
# Open_issues-mode DeepScan spec. RETAINED but NOT registered in
# ``PROVIDER_PROBES``: QZP runs the DeepScan gate in github_check_context mode
# (its result is a GitHub check context, not a token-polled API), so deepscan
# is EXEMPT here, not auth-probed (see EXEMPT_BLOCK_SCANNERS). This spec is the
# runtime-configured open_issues-mode probe — mirroring ``check_deepscan_zero.py``
# (Bearer-authed, deepscan.io-allowlisted, URL from ``DEEPSCAN_OPEN_ISSUES_URL``
# via ``url_env``) — kept so an open_issues-mode repo can re-register it without
# re-deriving the endpoint contract.
_DEEPSCAN = ProbeSpec(
    secret_env="DEEPSCAN_API_TOKEN",  # noqa: S106  # nosec — env-var NAME, not a secret
    request_url="",
    allowed_host_suffix="deepscan.io",
    auth_header="Authorization",
    auth_format="Bearer {token}",
    url_env="DEEPSCAN_OPEN_ISSUES_URL",
)

PROVIDER_PROBES: Dict[str, ProbeSpec] = {
    # SonarCloud (v2 ``sonarcloud`` + legacy ``sonar`` alias) → SONAR_TOKEN.
    "sonarcloud": _SONAR,
    "sonar": _SONAR,
    # Codacy provider-level whoami covers every facet: the v2 split into
    # issues/clones/complexity/coverage + the v1 ``codacy`` alias all read with
    # the same CODACY_API_TOKEN.
    "codacy": _CODACY,
    "codacy_issues": _CODACY,
    "codacy_clones": _CODACY,
    "codacy_complexity": _CODACY,
    "codacy_coverage": _CODACY,
    # Sentry org listing (authenticated) → SENTRY_AUTH_TOKEN.
    "sentry": _SENTRY,
}

# EXEMPT block scanners — NOT auth-probed, but recorded with a reason (never a
# silent skip). Each reason is sourced from the profile/codebase, not guessed.
EXEMPT_BLOCK_SCANNERS: Dict[str, str] = {
    # GitHub-native / in-CI — no external read token to probe.
    "codeql": "GitHub-native code scanning; no external read API token.",
    "dependabot": "GitHub-native dependency alerts; no external read API token.",
    "semgrep": "Runs in-CI via the semgrep action; no external read API token.",
    "qlty_check": "QLTY runs in-CI via its GitHub check; no external read API token.",
    "qlty": "Legacy alias of qlty_check (in-CI); no external read API token.",
    "socket_pr_alerts": "Socket GitHub App posts PR alerts in-CI; no read API token.",
    "deepscan": (
        "QZP runs the DeepScan gate in github_check_context mode "
        "(DEEPSCAN_POLICY_MODE=github_check_context); its result is a GitHub "
        "check context, not a token-polled API — no auth probe applies. The "
        "open_issues-mode DEEPSCAN_OPEN_ISSUES_URL/DEEPSCAN_API_TOKEN are not "
        "configured on this repo."
    ),
    # Token-shaped but NOT read-capable.
    "codecov": (
        "CODECOV_TOKEN is upload-only; the v2 read API returns 401/403 "
        "(see validate_codecov_flags.py:254-263) — no read-capable whoami."
    ),
    "coverage": (
        "The Coverage 100 Gate is a GitHub-native in-CI status check (not a "
        "Codecov API read); the upload-only CODECOV_TOKEN has no read whoami."
    ),
    "deepsource_visible": (
        "DEEPSOURCE_DSN is a Sentry-style upload DSN; the gate reads via HTML "
        "scrape. The GraphQL+DSN truthful read is TG-1 scope (A.CB-5)."
    ),
}


def _secret_value(env: Mapping[str, str], secret_env: str) -> str:
    """Return the stripped secret value for ``secret_env`` (``""`` if absent)."""
    return str(env.get(secret_env, "")).strip()


def _ok_result(provider: str, status: int) -> ProbeResult:
    """Build the ``ok`` result for a live token (2xx + accepted body)."""
    return ProbeResult(provider, "ok", status, f"{provider}: HTTP {status}")


def _secret_missing_result(provider: str, secret_env: str) -> ProbeResult:
    """Build the ``secret_missing`` result (no network call was made)."""
    return ProbeResult(
        provider,
        "secret_missing",
        None,
        f"{provider}: required secret {secret_env} absent",
    )


def _unreadable_http(provider: str, status: int) -> ProbeResult:
    """Build the ``unreadable`` result for a rejected probe (HTTP >= 400)."""
    return ProbeResult(provider, "unreadable", status, f"{provider}: HTTP {status}")


def _unreadable_invalid_body(provider: str, status: int) -> ProbeResult:
    """Build the ``unreadable`` result for a 2xx whose body says "not live".

    SonarCloud's ``validate`` 200s for a rotated token (``{"valid": false}``);
    the body predicate failing on a 2xx is a dead-token signal, not an ``ok``.
    """
    return ProbeResult(provider, "unreadable", status, f"{provider}: HTTP {status} (token rejected)")


def _unreadable_unreachable(provider: str) -> ProbeResult:
    """Build the ``unreadable`` result for an unreachable / blocked endpoint."""
    return ProbeResult(provider, "unreadable", None, f"{provider}: unreachable")


def _probe_url(spec: ProbeSpec, env: Mapping[str, str]) -> str:
    """Resolve the request URL: static ``request_url`` or the ``url_env`` value."""
    if spec.request_url:
        return spec.request_url
    return str(env.get(spec.url_env, "")).strip()


def _probe_headers(spec: ProbeSpec, token: str) -> Dict[str, str]:
    """Build the per-provider request headers (auth scheme varies by provider)."""
    return {
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
        spec.auth_header: spec.auth_format.format(token=token),
    }


def _run_probe(
    provider: str,
    spec: ProbeSpec,
    url: str,
    token: str,
    loader: Loader,
) -> ProbeResult:
    """Issue the authenticated probe and classify the outcome (no token leak)."""
    try:
        payload, _headers = loader(
            url,
            allowed_host_suffixes={spec.allowed_host_suffix},
            headers=_probe_headers(spec, token),
        )
    except urllib.error.HTTPError as exc:
        return _unreadable_http(provider, exc.code)
    except _UNREACHABLE_ERRORS:
        return _unreadable_unreachable(provider)
    if spec.accepts(payload):
        return _ok_result(provider, 200)
    return _unreadable_invalid_body(provider, 200)


def probe_provider(
    provider: str,
    *,
    env: Mapping[str, str],
    loader: Loader = load_json_https,
) -> ProbeResult:
    """Probe one auth-probed ``provider`` and return its truthful classification."""
    spec = PROVIDER_PROBES[provider]
    token = _secret_value(env, spec.secret_env)
    if not token:
        return _secret_missing_result(provider, spec.secret_env)
    url = _probe_url(spec, env)
    if not url:
        # Token present but no configured read endpoint (DeepScan needs
        # DEEPSCAN_OPEN_ISSUES_URL): cannot verify liveness → unreadable, loud.
        return ProbeResult(provider, "unreadable", None, f"{provider}: no read endpoint configured")
    return _run_probe(provider, spec, url, token, loader)


def _block_severity_scanners(profile: Mapping[str, Any]) -> List[str]:
    """Return the sorted names of block-severity scanners in ``profile``."""
    scanners = profile.get("scanners", {})
    return sorted(
        name for name, entry in scanners.items() if isinstance(entry, Mapping) and entry.get("severity") == "block"
    )


def _exempt_result(provider: str) -> ProbeResult:
    """Build the recorded (never silent) skip for an EXEMPT block scanner."""
    reason = EXEMPT_BLOCK_SCANNERS[provider]
    return ProbeResult(provider, "ok", None, f"{provider}: exempt — {reason}")


def _classify_block_scanner(
    provider: str,
    *,
    env: Mapping[str, str],
    loader: Loader,
) -> ProbeResult:
    """Route one block scanner to a probe, a recorded exempt skip, or a raise."""
    if provider in PROVIDER_PROBES:
        return probe_provider(provider, env=env, loader=loader)
    if provider in EXEMPT_BLOCK_SCANNERS:
        return _exempt_result(provider)
    raise UnclassifiedScannerError(
        f"block-severity scanner {provider!r} is in neither PROVIDER_PROBES "
        "nor EXEMPT_BLOCK_SCANNERS — classify it (probe or exempt-with-reason) "
        "instead of skipping it silently."
    )


def run_preflight(
    profile: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    loader: Loader = load_json_https,
) -> List[ProbeResult]:
    """Classify every block-severity scanner in ``profile`` (never silent)."""
    return [_classify_block_scanner(provider, env=env, loader=loader) for provider in _block_severity_scanners(profile)]


def _exit_code(results: Sequence[ProbeResult]) -> int:
    """Apply fail-closed precedence: unreadable(2) > secret_missing(1) > ok(0)."""
    outcomes = {result.outcome for result in results}
    if "unreadable" in outcomes:
        return EXIT_UNREADABLE
    if "secret_missing" in outcomes:
        return EXIT_SECRET_MISSING
    return EXIT_OK


def _open_unavailable_alerts(
    slug: str,
    results: Sequence[ProbeResult],
    *,
    opener: AlertOpener,
) -> None:
    """Open one ``alert:scanner-unavailable`` per unreadable provider (deduped)."""
    for result in results:
        if result.outcome != "unreadable":
            continue
        opener(
            slug,
            alert_type=alerts.AlertType.SCANNER_UNAVAILABLE,
            subject=f"{slug}:{result.provider}",
            body=(
                f"Block-severity scanner **{result.provider}** on `{slug}` has "
                "its secret wired but the authenticated preflight probe failed: "
                f"`{result.diagnostic}`. The read token is likely rotated or "
                "revoked — rotate it in the repo/org secrets so live dashboard "
                "reads stop silently failing."
            ),
        )


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    """Parse the preflight CLI arguments."""
    parser = argparse.ArgumentParser(description="Token-rotation preflight: probe block-severity scanner tokens.")
    parser.add_argument(
        "--profile",
        required=True,
        help="Repo slug whose resolved profile drives the block-scanner set.",
    )
    parser.add_argument(
        "--open-alerts",
        action="store_true",
        help="Open alert:scanner-unavailable issues for unreadable providers.",
    )
    parser.add_argument(
        "--platform-slug",
        default=DEFAULT_PLATFORM_SLUG,
        help="Platform repo to open alert issues on.",
    )
    return parser.parse_args(argv)


def _resolve_repo_slug(inventory: Mapping[str, Any], profile: str) -> str:
    """Map a full repo slug OR a short profile id to the inventory repo slug.

    The plan invokes ``--profile quality-zero-platform`` (the profile id), but
    ``load_repo_profile`` keys on the full repo slug (``Prekzursil/...``).
    Accept either; raise ``KeyError`` loudly when neither matches so an unknown
    target never resolves silently.
    """
    for repo in inventory.get("repos", []):
        if repo.get("slug") == profile or repo.get("profile") == profile:
            return str(repo["slug"])
    raise KeyError(f"No inventory repo matches profile id or slug {profile!r}")


def _resolve_profile(profile: str) -> Mapping[str, Any]:
    """Resolve one repo profile via the control plane (default profile loader)."""
    inventory = control_plane.load_inventory()
    return control_plane.load_repo_profile(inventory, _resolve_repo_slug(inventory, profile))


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    loader: Loader = load_json_https,
    profile_loader: ProfileLoader = _resolve_profile,
    alert_opener: AlertOpener = alerts.open_alert_issue,
) -> int:
    """CLI entry point. Exit 2 if any unreadable, else 1 if any secret-missing, else 0."""
    args = _parse_args(argv)
    resolved_env = os.environ if env is None else env
    profile = profile_loader(args.profile)
    results = run_preflight(profile, env=resolved_env, loader=loader)
    if args.open_alerts:
        _open_unavailable_alerts(args.platform_slug, results, opener=alert_opener)
    return _exit_code(results)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
