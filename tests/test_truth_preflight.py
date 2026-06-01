"""Tests for TG-2 — token-rotation preflight (``scripts.quality.truth.preflight``).

The preflight makes the campaign's master blocker — rotated SaaS tokens —
**loud instead of silent**. For every block-severity scanner in the resolved
profile it either (a) auth-probes a read-capable token, (b) records a
checked-in EXEMPT skip *with a reason* (never silent), or (c) RAISES when a
block scanner is in NEITHER set.

TDD order mirrors ``docs/plans/2026-06-01-truthful-gate-tg2-token-preflight-plan.md``
(items 1-13). Every probe is mocked: the four live provider secrets live only
in CI, so the live exit-0 acceptance is verified on the PR, not locally.
"""

from __future__ import absolute_import

import ssl
import unittest
import urllib.error
from typing import Any, Dict, List, Mapping
from unittest.mock import MagicMock

from scripts.quality.truth import preflight


def _http_error(code: int) -> urllib.error.HTTPError:
    """Build an ``HTTPError`` double mirroring ``load_json_https`` on >=400."""
    return urllib.error.HTTPError(
        url="https://example.invalid/api",
        code=code,
        msg="boom",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )


def _ok_loader(*_args: Any, **_kwargs: Any) -> Any:
    """A loader double returning a successful 2xx JSON payload tuple."""
    return ({"valid": True, "login": "svc"}, {})


def _raising_loader(exc: BaseException) -> Any:
    """Build a loader double that raises ``exc`` when invoked."""

    def _loader(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    return _loader


def _block_profile(scanner_names: List[str]) -> Dict[str, Any]:
    """Build a minimal resolved-profile shape with the given block scanners."""
    return {"scanners": {name: {"severity": "block"} for name in scanner_names}}


class ProbeProviderHappyPathTests(unittest.TestCase):
    """``probe_provider`` returns ``ok`` when the authenticated probe is 2xx."""

    def test_probe_ok_when_authenticated(self) -> None:
        """A 2xx probe (loader returns a dict) → ``ok`` outcome, status 200."""
        result = preflight.probe_provider(
            "sonarcloud",
            env={"SONAR_TOKEN": "tok"},
            loader=_ok_loader,
        )
        self.assertEqual(result.outcome, "ok")
        self.assertEqual(result.http_status, 200)
        self.assertEqual(result.provider, "sonarcloud")


class ProbeRequestShapeTests(unittest.TestCase):
    """The loader is called with the exact URL, allowlist, and auth header.

    These assertions are the local contract for *what each provider hits* and
    *how it authenticates* — the part the live-CI exit-0 acceptance cannot
    cover offline. Codacy in particular must use ``app.codacy.com`` + the
    ``api-token`` header (NOT ``Authorization: Bearer``).
    """

    @staticmethod
    def _capture(provider: str, env: Mapping[str, str]) -> Any:
        loader = MagicMock(return_value=({"valid": True}, {}))
        preflight.probe_provider(provider, env=dict(env), loader=loader)
        loader.assert_called_once()
        return loader.call_args

    def test_sonar_url_allowlist_and_bearer_header(self) -> None:
        """SonarCloud hits sonarcloud.io with a Bearer header."""
        args = self._capture("sonarcloud", {"SONAR_TOKEN": "tok"})
        url = args.args[0]
        kwargs = args.kwargs
        self.assertEqual(url, "https://sonarcloud.io/api/authentication/validate")
        self.assertEqual(kwargs["allowed_host_suffixes"], {"sonarcloud.io"})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")

    def test_codacy_uses_app_host_and_api_token_header(self) -> None:
        """Codacy uses app.codacy.com + the ``api-token`` header (not Bearer)."""
        args = self._capture("codacy", {"CODACY_API_TOKEN": "tok"})
        url = args.args[0]
        kwargs = args.kwargs
        self.assertTrue(url.startswith("https://app.codacy.com/api/v3/user"))
        self.assertEqual(kwargs["allowed_host_suffixes"], {"codacy.com"})
        self.assertEqual(kwargs["headers"]["api-token"], "tok")
        self.assertNotIn("Authorization", kwargs["headers"])

    def test_sentry_uses_authenticated_org_endpoint_with_bearer(self) -> None:
        """Sentry hits an authenticated /api/0 path that 401s on a dead token."""
        args = self._capture("sentry", {"SENTRY_AUTH_TOKEN": "tok"})
        url = args.args[0]
        kwargs = args.kwargs
        self.assertTrue(url.startswith("https://sentry.io/api/0/organizations"))
        self.assertEqual(kwargs["allowed_host_suffixes"], {"sentry.io"})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")

    def test_deepscan_mirrors_configurable_open_issues_url(self) -> None:
        """DeepScan mirrors check_deepscan_zero: reads DEEPSCAN_OPEN_ISSUES_URL."""
        url_value = "https://api.deepscan.io/api/projects/acme/issues"
        args = self._capture(
            "deepscan",
            {"DEEPSCAN_API_TOKEN": "tok", "DEEPSCAN_OPEN_ISSUES_URL": url_value},
        )
        url = args.args[0]
        kwargs = args.kwargs
        self.assertEqual(url, url_value)
        self.assertEqual(kwargs["allowed_host_suffixes"], {"deepscan.io"})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")

    def test_deepscan_without_url_is_unreadable_not_silent(self) -> None:
        """DeepScan token present but no configured read URL → unreadable (loud)."""
        loader = MagicMock()
        result = preflight.probe_provider(
            "deepscan",
            env={"DEEPSCAN_API_TOKEN": "tok"},
            loader=loader,
        )
        self.assertEqual(result.outcome, "unreadable")
        self.assertIsNone(result.http_status)
        loader.assert_not_called()


class ProbeBodyValidityTests(unittest.TestCase):
    """SonarCloud's ``validate`` returns HTTP 200 even for a rotated token."""

    def test_sonar_http_200_with_valid_false_is_unreadable(self) -> None:
        """200 + ``{"valid": false}`` (rotated Sonar token) → ``unreadable``.

        ``GET api/authentication/validate`` answers 200 regardless of token
        liveness; the body carries the verdict. Trusting 2xx alone would mark a
        rotated token ``ok`` — the exact false-negative TG-2 exists to kill.
        """
        loader = MagicMock(return_value=({"valid": False}, {}))
        result = preflight.probe_provider(
            "sonarcloud", env={"SONAR_TOKEN": "tok"}, loader=loader,
        )
        self.assertEqual(result.outcome, "unreadable")
        self.assertEqual(result.http_status, 200)

    def test_sonar_http_200_with_valid_true_is_ok(self) -> None:
        """200 + ``{"valid": true}`` (live Sonar token) → ``ok``."""
        loader = MagicMock(return_value=({"valid": True}, {}))
        result = preflight.probe_provider(
            "sonarcloud", env={"SONAR_TOKEN": "tok"}, loader=loader,
        )
        self.assertEqual(result.outcome, "ok")

    def test_provider_without_body_predicate_accepts_any_2xx(self) -> None:
        """Codacy (no body predicate) treats any 2xx payload as ``ok``."""
        loader = MagicMock(return_value=({"data": {}}, {}))
        result = preflight.probe_provider(
            "codacy", env={"CODACY_API_TOKEN": "tok"}, loader=loader,
        )
        self.assertEqual(result.outcome, "ok")


class ProbeProviderUnreadableTests(unittest.TestCase):
    """A present-but-rejected/unreachable token → ``unreadable`` (exit-2 class)."""

    def test_probe_unreadable_on_http_401(self) -> None:
        """``HTTPError(401)`` → ``unreadable`` with ``http_status`` set."""
        result = preflight.probe_provider(
            "codacy",
            env={"CODACY_API_TOKEN": "tok"},
            loader=_raising_loader(_http_error(401)),
        )
        self.assertEqual(result.outcome, "unreadable")
        self.assertEqual(result.http_status, 401)

    def test_probe_unreadable_on_http_403(self) -> None:
        """``HTTPError(403)`` → ``unreadable`` with ``http_status`` set."""
        result = preflight.probe_provider(
            "sentry",
            env={"SENTRY_AUTH_TOKEN": "tok"},
            loader=_raising_loader(_http_error(403)),
        )
        self.assertEqual(result.outcome, "unreadable")
        self.assertEqual(result.http_status, 403)

    def test_probe_unreadable_on_unreachable_urlerror(self) -> None:
        """``URLError`` → ``unreadable`` with ``http_status`` None."""
        result = preflight.probe_provider(
            "deepscan",
            env={"DEEPSCAN_API_TOKEN": "tok"},
            loader=_raising_loader(urllib.error.URLError("dns")),
        )
        self.assertEqual(result.outcome, "unreadable")
        self.assertIsNone(result.http_status)

    def test_probe_unreadable_on_timeout(self) -> None:
        """``TimeoutError`` → ``unreadable`` with ``http_status`` None."""
        result = preflight.probe_provider(
            "sonarcloud",
            env={"SONAR_TOKEN": "tok"},
            loader=_raising_loader(TimeoutError("slow")),
        )
        self.assertEqual(result.outcome, "unreadable")
        self.assertIsNone(result.http_status)

    def test_probe_unreadable_on_oserror(self) -> None:
        """``OSError`` → ``unreadable`` with ``http_status`` None."""
        result = preflight.probe_provider(
            "sonarcloud",
            env={"SONAR_TOKEN": "tok"},
            loader=_raising_loader(OSError("socket")),
        )
        self.assertEqual(result.outcome, "unreadable")
        self.assertIsNone(result.http_status)

    def test_probe_unreadable_on_sslerror(self) -> None:
        """``ssl.SSLError`` → ``unreadable`` with ``http_status`` None."""
        result = preflight.probe_provider(
            "sonarcloud",
            env={"SONAR_TOKEN": "tok"},
            loader=_raising_loader(ssl.SSLError("tls")),
        )
        self.assertEqual(result.outcome, "unreadable")
        self.assertIsNone(result.http_status)

    def test_probe_unreadable_on_allowlist_reject_valueerror(self) -> None:
        """``ValueError`` (SSRF/allowlist reject in normalize) → ``unreadable``, None."""
        result = preflight.probe_provider(
            "sonarcloud",
            env={"SONAR_TOKEN": "tok"},
            loader=_raising_loader(ValueError("host not in allowlist")),
        )
        self.assertEqual(result.outcome, "unreadable")
        self.assertIsNone(result.http_status)


class ProbeProviderSecretMissingTests(unittest.TestCase):
    """A required secret absent from env → ``secret_missing`` (no network call)."""

    def test_probe_secret_missing_when_env_absent(self) -> None:
        """Absent secret → ``secret_missing`` and the loader is never called."""
        loader = MagicMock()
        result = preflight.probe_provider(
            "sonarcloud",
            env={},
            loader=loader,
        )
        self.assertEqual(result.outcome, "secret_missing")
        self.assertIsNone(result.http_status)
        loader.assert_not_called()

    def test_probe_secret_missing_when_env_blank(self) -> None:
        """Whitespace-only secret is treated as absent → ``secret_missing``."""
        loader = MagicMock()
        result = preflight.probe_provider(
            "codacy",
            env={"CODACY_API_TOKEN": "   "},
            loader=loader,
        )
        self.assertEqual(result.outcome, "secret_missing")
        loader.assert_not_called()


class DiagnosticNonLeakTests(unittest.TestCase):
    """``diagnostic`` must never carry the token, URL, or query string."""

    def test_diagnostic_never_contains_token_or_url(self) -> None:
        """Across ok/unreadable, the secret literal and host/query never leak."""
        secret = "super-secret-token-value-12345"
        ok = preflight.probe_provider(
            "sonarcloud", env={"SONAR_TOKEN": secret}, loader=_ok_loader,
        )
        rejected = preflight.probe_provider(
            "sonarcloud",
            env={"SONAR_TOKEN": secret},
            loader=_raising_loader(_http_error(401)),
        )
        unreachable = preflight.probe_provider(
            "sonarcloud",
            env={"SONAR_TOKEN": secret},
            loader=_raising_loader(urllib.error.URLError("dns")),
        )
        spec = preflight.PROVIDER_PROBES["sonarcloud"]
        # Guard against the latent ``assertNotIn("", s)`` trap if this is ever
        # parametrized over a provider whose request_url is env-resolved (empty).
        self.assertTrue(spec.request_url)
        for result in (ok, rejected, unreachable):
            self.assertNotIn(secret, result.diagnostic)
            # Neither the request host nor the request URL/path leaks.
            self.assertNotIn(spec.request_url, result.diagnostic)
            self.assertNotIn("sonarcloud.io", result.diagnostic)
            self.assertNotIn("https://", result.diagnostic)
        self.assertIn("sonarcloud", rejected.diagnostic)
        self.assertIn("401", rejected.diagnostic)
        self.assertIn("unreachable", unreachable.diagnostic)


class ProbeSpecContractTests(unittest.TestCase):
    """The probe table and exempt set are mandatory, non-empty, and SSRF-guarded."""

    def test_allowed_host_suffix_mandatory_non_empty(self) -> None:
        """Every probe spec carries a non-empty allowlist suffix (SSRF guard)."""
        for name, spec in preflight.PROVIDER_PROBES.items():
            self.assertTrue(
                spec.allowed_host_suffix, f"{name} has empty host suffix",
            )
            self.assertTrue(spec.secret_env, f"{name} has empty secret env")
            self.assertTrue(spec.auth_header, f"{name} has empty auth header")

    def test_four_distinct_tokens_back_the_probe_table(self) -> None:
        """The 9 probe names collapse onto exactly the 4 read-capable tokens."""
        tokens = {spec.secret_env for spec in preflight.PROVIDER_PROBES.values()}
        self.assertEqual(
            tokens,
            {"SONAR_TOKEN", "CODACY_API_TOKEN", "SENTRY_AUTH_TOKEN", "DEEPSCAN_API_TOKEN"},
        )

    def test_exempt_entries_each_carry_a_reason(self) -> None:
        """Every EXEMPT scanner records a non-empty human reason (not silent)."""
        for name, reason in preflight.EXEMPT_BLOCK_SCANNERS.items():
            self.assertTrue(reason.strip(), f"{name} exempt without a reason")

    def test_probe_and_exempt_sets_are_disjoint(self) -> None:
        """No scanner is both auth-probed and exempt."""
        overlap = set(preflight.PROVIDER_PROBES) & set(preflight.EXEMPT_BLOCK_SCANNERS)
        self.assertEqual(overlap, set())


class RunPreflightTests(unittest.TestCase):
    """``run_preflight`` probes block scanners and never silently skips."""

    def test_run_preflight_probes_only_block_severity(self) -> None:
        """Info / non-block scanners are not probed; block providers are."""
        profile = {
            "scanners": {
                "sonarcloud": {"severity": "block"},
                "socket_project_report": {"severity": "info"},
            }
        }
        results = preflight.run_preflight(
            profile, env={"SONAR_TOKEN": "tok"}, loader=_ok_loader,
        )
        providers = {r.provider for r in results}
        self.assertIn("sonarcloud", providers)
        self.assertNotIn("socket_project_report", providers)
        self.assertEqual(len(results), 1)

    def test_run_preflight_raises_on_unmapped_block_scanner(self) -> None:
        """A block scanner in NEITHER PROBES NOR EXEMPT raises loudly."""
        profile = _block_profile(["totally_new_block_scanner"])
        with self.assertRaises(preflight.UnclassifiedScannerError) as ctx:
            preflight.run_preflight(profile, env={}, loader=_ok_loader)
        self.assertIn("totally_new_block_scanner", str(ctx.exception))

    def test_run_preflight_skips_exempt_block_scanner_with_reason(self) -> None:
        """An exempt block scanner is recorded (not silent) and not probed."""
        loader = MagicMock()
        profile = _block_profile(["codeql"])
        results = preflight.run_preflight(profile, env={}, loader=loader)
        loader.assert_not_called()
        self.assertEqual(len(results), 1)
        record = results[0]
        self.assertEqual(record.provider, "codeql")
        self.assertEqual(record.outcome, "ok")
        self.assertIn("exempt", record.diagnostic.lower())

    def test_run_preflight_resolved_qzp_profile_does_not_raise(self) -> None:
        """The real resolved QZP self-profile classifies every block scanner.

        Local proxy for the CI exit-0 acceptance: every block-severity scanner
        in the live profile must be in PROBES or EXEMPT. Fails loudly if the
        profile later grows an unclassified block scanner.
        """
        from scripts.quality import control_plane

        inventory = control_plane.load_inventory()
        profile = control_plane.load_repo_profile(
            inventory, "Prekzursil/quality-zero-platform"
        )
        results = preflight.run_preflight(
            profile,
            env={
                "SONAR_TOKEN": "tok",
                "CODACY_API_TOKEN": "tok",
                "SENTRY_AUTH_TOKEN": "tok",
                "DEEPSCAN_API_TOKEN": "tok",
                "DEEPSCAN_OPEN_ISSUES_URL": "https://api.deepscan.io/api/projects/acme/issues",
            },
            loader=_ok_loader,
        )
        outcomes = {r.outcome for r in results}
        self.assertNotIn("unreadable", outcomes)
        self.assertNotIn("secret_missing", outcomes)


class ResolveProfileDefaultLoaderTests(unittest.TestCase):
    """The default ``profile_loader`` resolves a real profile via the control plane."""

    def test_resolve_profile_accepts_full_slug(self) -> None:
        """``_resolve_profile`` reads the QZP self-profile by full repo slug."""
        profile = preflight._resolve_profile("Prekzursil/quality-zero-platform")
        block = preflight._block_severity_scanners(profile)
        self.assertIn("sonarcloud", block)
        self.assertIn("codeql", block)

    def test_resolve_profile_accepts_short_profile_id(self) -> None:
        """The plan's ``--profile quality-zero-platform`` short id resolves too."""
        profile = preflight._resolve_profile("quality-zero-platform")
        self.assertEqual(profile["slug"], "Prekzursil/quality-zero-platform")

    def test_resolve_profile_unknown_raises_keyerror(self) -> None:
        """An unknown profile id / slug raises ``KeyError`` (loud, not silent)."""
        with self.assertRaises(KeyError):
            preflight._resolve_profile("no-such-repo-anywhere")

    def test_main_uses_default_profile_loader(self) -> None:
        """``main`` with no injected ``profile_loader`` hits the control plane.

        Exercises the default DI path end-to-end: every block scanner in the
        resolved QZP profile is classified (no raise) and, with the four tokens
        present + an ``ok`` loader, the run exits 0.
        """
        code = preflight.main(
            ["--profile", "quality-zero-platform"],
            env={
                "SONAR_TOKEN": "tok",
                "CODACY_API_TOKEN": "tok",
                "SENTRY_AUTH_TOKEN": "tok",
                "DEEPSCAN_API_TOKEN": "tok",
                "DEEPSCAN_OPEN_ISSUES_URL": "https://api.deepscan.io/api/projects/acme/issues",
            },
            loader=_ok_loader,
        )
        self.assertEqual(code, 0)


class MainExitCodeTests(unittest.TestCase):
    """``main`` precedence: unreadable(2) dominates secret_missing(1) over ok(0)."""

    def _run_main(
        self, scanners: Mapping[str, str], env: Mapping[str, str], loader: Any,
    ) -> int:
        profile = {"scanners": {n: {"severity": s} for n, s in scanners.items()}}
        loaded = MagicMock(return_value=profile)
        return preflight.main(
            ["--profile", "Prekzursil/quality-zero-platform"],
            env=dict(env),
            loader=loader,
            profile_loader=loaded,
        )

    def test_main_exit_0_on_all_ok(self) -> None:
        """All providers ``ok`` → exit 0."""
        code = self._run_main(
            {"sonarcloud": "block"}, {"SONAR_TOKEN": "tok"}, _ok_loader,
        )
        self.assertEqual(code, 0)

    def test_main_exit_1_on_secret_missing(self) -> None:
        """A missing secret (no unreadable) → exit 1."""
        code = self._run_main(
            {"sonarcloud": "block"}, {}, _ok_loader,
        )
        self.assertEqual(code, 1)

    def test_main_exit_2_on_unreadable(self) -> None:
        """An unreadable provider → exit 2."""
        code = self._run_main(
            {"sonarcloud": "block"},
            {"SONAR_TOKEN": "tok"},
            _raising_loader(_http_error(401)),
        )
        self.assertEqual(code, 2)

    def test_main_unreadable_dominates_secret_missing(self) -> None:
        """One unreadable + one secret-missing → exit 2 (unreadable dominates)."""
        code = self._run_main(
            {"sonarcloud": "block", "sentry": "block"},
            {"SONAR_TOKEN": "tok"},  # SENTRY_AUTH_TOKEN absent → secret_missing
            _raising_loader(_http_error(403)),  # sonarcloud → unreadable
        )
        self.assertEqual(code, 2)


class MainOpenAlertsTests(unittest.TestCase):
    """``--open-alerts`` fires ``alert:scanner-unavailable`` for unreadable probes."""

    def test_main_open_alerts_opens_scanner_unavailable(self) -> None:
        """An unreadable provider with ``--open-alerts`` opens the new alert."""
        profile = {"scanners": {"sonarcloud": {"severity": "block"}}}
        opener = MagicMock(return_value={"number": 1, "title": "t", "created": True})
        code = preflight.main(
            ["--profile", "Prekzursil/quality-zero-platform", "--open-alerts"],
            env={"SONAR_TOKEN": "tok"},
            loader=_raising_loader(_http_error(401)),
            profile_loader=MagicMock(return_value=profile),
            alert_opener=opener,
        )
        self.assertEqual(code, 2)
        opener.assert_called_once()
        _, kwargs = opener.call_args
        from scripts.quality import alerts

        self.assertEqual(kwargs["alert_type"], alerts.AlertType.SCANNER_UNAVAILABLE)

    def test_main_open_alerts_noop_when_all_ok(self) -> None:
        """No unreadable provider → ``--open-alerts`` opens nothing."""
        profile = {"scanners": {"sonarcloud": {"severity": "block"}}}
        opener = MagicMock()
        code = preflight.main(
            ["--profile", "Prekzursil/quality-zero-platform", "--open-alerts"],
            env={"SONAR_TOKEN": "tok"},
            loader=_ok_loader,
            profile_loader=MagicMock(return_value=profile),
            alert_opener=opener,
        )
        self.assertEqual(code, 0)
        opener.assert_not_called()

    def test_main_unreadable_without_open_alerts_skips_opener(self) -> None:
        """Without ``--open-alerts`` an unreadable provider opens no issue."""
        profile = {"scanners": {"sonarcloud": {"severity": "block"}}}
        opener = MagicMock()
        code = preflight.main(
            ["--profile", "Prekzursil/quality-zero-platform"],
            env={"SONAR_TOKEN": "tok"},
            loader=_raising_loader(_http_error(401)),
            profile_loader=MagicMock(return_value=profile),
            alert_opener=opener,
        )
        self.assertEqual(code, 2)
        opener.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
