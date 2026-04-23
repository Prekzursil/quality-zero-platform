"""Unit coverage for ``scripts.quality.validate_codecov_flags``.

Phase 2 of docs/QZP-V2-DESIGN.md ships the per-flag upload loop in
reusable-codecov-analytics.yml. The validator is the second half of
§5.1 — these tests pin the parsing, URL construction, Codecov-shape
tolerance, polling/backoff behaviour, and CLI exit contract so future
refactors cannot silently drop the ingest-drop detector.
"""

from __future__ import absolute_import

import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from scripts.quality import validate_codecov_flags


def _write_temp_json(payload: object, cleanup: unittest.TestCase) -> Path:
    """Return a path to a temp profile.json, scheduled for cleanup.

    Uses ``tempfile.mkstemp`` so the file descriptor is explicitly closed
    — DeepSource PYL-R1732 flags ``NamedTemporaryFile(delete=False, ...)``
    left open. The returned ``Path`` is registered with the test case's
    ``addCleanup`` so the tempfile is removed regardless of assertion
    outcome.
    """
    fd, name = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    tmp = Path(name)
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    cleanup.addCleanup(tmp.unlink, missing_ok=True)
    return tmp


class DeclaredFlagsTests(unittest.TestCase):
    """``_declared_flags`` parses the profile JSON emitted by ``export_profile``."""

    def _write(self, payload: dict) -> Path:
        """Return a path to a temp profile.json with ``payload`` serialised."""
        return _write_temp_json(payload, self)

    def test_returns_flag_value_when_present(self) -> None:
        """``flag`` is preferred over ``name`` when both exist."""
        path = self._write({
            "coverage": {"inputs": [
                {"name": "ui-raw", "flag": "ui", "path": "ui/cov.xml"},
            ]}
        })
        self.assertEqual(validate_codecov_flags._declared_flags(path), ["ui"])

    def test_falls_back_to_name_when_flag_missing(self) -> None:
        """An input without ``flag`` reuses ``name`` as the canonical key."""
        path = self._write({
            "coverage": {"inputs": [{"name": "backend", "path": "cov.xml"}]}
        })
        self.assertEqual(validate_codecov_flags._declared_flags(path), ["backend"])

    def test_skips_non_dict_entries(self) -> None:
        """Defensive: string/None entries in ``inputs`` are ignored."""
        path = self._write({
            "coverage": {"inputs": ["nope", None, {"name": "ok", "flag": "ok"}]}
        })
        self.assertEqual(validate_codecov_flags._declared_flags(path), ["ok"])

    def test_deduplicates_while_preserving_order(self) -> None:
        """Repeated flags appear once and keep first-seen ordering."""
        path = self._write({
            "coverage": {"inputs": [
                {"flag": "b", "path": "a"},
                {"flag": "a", "path": "b"},
                {"flag": "b", "path": "c"},
            ]}
        })
        self.assertEqual(validate_codecov_flags._declared_flags(path), ["b", "a"])

    def test_missing_coverage_or_inputs_yields_empty(self) -> None:
        """Profiles without coverage/inputs return ``[]`` rather than crashing."""
        self.assertEqual(
            validate_codecov_flags._declared_flags(self._write({})), []
        )
        self.assertEqual(
            validate_codecov_flags._declared_flags(self._write({"coverage": {}})),
            [],
        )
        self.assertEqual(
            validate_codecov_flags._declared_flags(
                self._write({"coverage": {"inputs": []}})
            ),
            [],
        )

    def test_top_level_non_dict_yields_empty(self) -> None:
        """A JSON file that isn't an object (e.g. an array) returns ``[]``."""
        path = self._write([])  # type: ignore[arg-type]
        self.assertEqual(validate_codecov_flags._declared_flags(path), [])

    def test_blank_flag_and_name_skipped(self) -> None:
        """Items with both ``flag`` and ``name`` blank are dropped."""
        path = self._write({
            "coverage": {"inputs": [
                {"flag": "", "name": "", "path": "a.xml"},
                {"flag": "ok", "path": "b.xml"},
            ]}
        })
        self.assertEqual(validate_codecov_flags._declared_flags(path), ["ok"])


class SlugShaValidationTests(unittest.TestCase):
    """``_validate_slug`` + ``_validate_sha`` reject URL-smuggling attempts."""

    def test_valid_slug_accepted(self) -> None:
        """``owner/name`` with safe chars does not raise."""
        validate_codecov_flags._validate_slug("Prekzursil/event-link")

    def test_slug_without_slash_rejected(self) -> None:
        """A slug missing the owner/name separator is rejected."""
        with self.assertRaises(ValueError):
            validate_codecov_flags._validate_slug("nobody")

    def test_slug_with_two_slashes_rejected(self) -> None:
        """Extra slashes could smuggle path segments — rejected."""
        with self.assertRaises(ValueError):
            validate_codecov_flags._validate_slug("a/b/c")

    def test_slug_with_spaces_rejected(self) -> None:
        """Spaces aren't in the allowlist and must be rejected."""
        with self.assertRaises(ValueError):
            validate_codecov_flags._validate_slug("has space/name")

    def test_slug_with_percent_rejected(self) -> None:
        """URL-encoding via ``%2f`` must not bypass the allowlist."""
        with self.assertRaises(ValueError):
            validate_codecov_flags._validate_slug("owner/na%2fme")

    def test_empty_slug_rejected(self) -> None:
        """The empty string has no ``/`` and is rejected."""
        with self.assertRaises(ValueError):
            validate_codecov_flags._validate_slug("")

    def test_valid_sha_accepted(self) -> None:
        """A 40-char hex SHA passes validation."""
        validate_codecov_flags._validate_sha("a" * 40)

    def test_short_sha_accepted(self) -> None:
        """Short SHA prefixes (still hex) are allowed."""
        validate_codecov_flags._validate_sha("abc123")

    def test_non_hex_sha_rejected(self) -> None:
        """A non-hex char in the SHA is rejected."""
        with self.assertRaises(ValueError):
            validate_codecov_flags._validate_sha("g" * 40)

    def test_empty_sha_rejected(self) -> None:
        """An empty SHA is rejected."""
        with self.assertRaises(ValueError):
            validate_codecov_flags._validate_sha("")

    def test_overlong_sha_rejected(self) -> None:
        """A SHA longer than 64 chars is rejected."""
        with self.assertRaises(ValueError):
            validate_codecov_flags._validate_sha("a" * 65)


class CommitUrlTests(unittest.TestCase):
    """``_codecov_commit_url`` interpolates validated args under the API prefix."""

    def test_valid_inputs_produce_expected_url(self) -> None:
        """Happy path: the constructed URL matches the Codecov v2 shape."""
        url = validate_codecov_flags._codecov_commit_url(
            "Prekzursil/event-link", "abc123def"
        )
        self.assertEqual(
            url,
            "https://api.codecov.io/api/v2/github/Prekzursil/"
            "repos/event-link/commits/abc123def/",
        )

    def test_bad_slug_rejected(self) -> None:
        """Slug validation is enforced before URL construction."""
        with self.assertRaises(ValueError):
            validate_codecov_flags._codecov_commit_url("bad slug", "a" * 40)

    def test_bad_sha_rejected(self) -> None:
        """SHA validation is enforced before URL construction."""
        with self.assertRaises(ValueError):
            validate_codecov_flags._codecov_commit_url("ok/repo", "zzz")


class FetchCodecovReportTests(unittest.TestCase):
    """``_fetch_codecov_report`` routes through ``load_bytes_https``."""

    def test_refuses_non_codecov_url(self) -> None:
        """Defence-in-depth: raises if URL escaped the allowed prefix."""
        with self.assertRaises(ValueError):
            validate_codecov_flags._fetch_codecov_report(
                "https://evil.example.com/", ""
            )

    def test_sends_authorization_header_when_token_set(self) -> None:
        """A non-empty token populates the ``Authorization`` header."""
        captured: dict = {}

        def fake_loader(url: str, **kwargs) -> tuple:
            """Capture kwargs so the test can assert on headers."""
            captured["url"] = url
            captured["headers"] = kwargs.get("headers", {})
            captured["allowed_hosts"] = kwargs.get("allowed_hosts")
            return (b'{"totals": {}}', {})

        with patch(
            "scripts.quality.validate_codecov_flags.load_bytes_https",
            side_effect=fake_loader,
        ):
            result = validate_codecov_flags._fetch_codecov_report(
                "https://api.codecov.io/api/v2/github/x/repos/y/commits/abc/",
                "test-bearer-abc",
            )
        self.assertEqual(result, {"totals": {}})
        self.assertEqual(
            captured["headers"]["Authorization"], "Bearer test-bearer-abc"
        )
        self.assertEqual(captured["headers"]["Accept"], "application/json")
        self.assertEqual(captured["allowed_hosts"], {"api.codecov.io"})

    def test_omits_authorization_when_token_blank(self) -> None:
        """A blank token leaves ``Authorization`` off the request."""
        captured: dict = {}

        def fake_loader(url: str, **kwargs) -> tuple:
            """Capture headers for the assertion."""
            captured["headers"] = kwargs.get("headers", {})
            return (b"{}", {})

        with patch(
            "scripts.quality.validate_codecov_flags.load_bytes_https",
            side_effect=fake_loader,
        ):
            validate_codecov_flags._fetch_codecov_report(
                "https://api.codecov.io/api/v2/github/x/repos/y/commits/abc/",
                "",
            )
        self.assertNotIn("Authorization", captured["headers"])


class FlagsPresentInReportTests(unittest.TestCase):
    """``_flags_present_in_report`` tolerates both Codecov schema shapes."""

    def test_totals_flags_list_shape(self) -> None:
        """Older shape: ``totals.flags`` is a list of ``{name: str}``."""
        report = {"totals": {"flags": [{"name": "backend"}, {"name": "ui"}]}}
        self.assertEqual(
            validate_codecov_flags._flags_present_in_report(report),
            {"backend", "ui"},
        )

    def test_inner_report_flags_dict_shape(self) -> None:
        """Newer shape: ``report.flags`` is a dict keyed by flag name."""
        report = {"report": {"flags": {"backend": {}, "ui": {}}}}
        self.assertEqual(
            validate_codecov_flags._flags_present_in_report(report),
            {"backend", "ui"},
        )

    def test_both_shapes_union_returned(self) -> None:
        """If both shapes are present, flags from both merge."""
        report = {
            "totals": {"flags": [{"name": "a"}]},
            "report": {"flags": {"b": {}}},
        }
        self.assertEqual(
            validate_codecov_flags._flags_present_in_report(report),
            {"a", "b"},
        )

    def test_empty_report_yields_empty_set(self) -> None:
        """Unknown schema returns an empty set rather than raising."""
        self.assertEqual(
            validate_codecov_flags._flags_present_in_report({}), set()
        )

    def test_non_dict_flag_entries_ignored(self) -> None:
        """String entries under ``totals.flags`` are defensively skipped."""
        report = {"totals": {"flags": ["weird", {"name": "ok"}]}}
        self.assertEqual(
            validate_codecov_flags._flags_present_in_report(report), {"ok"}
        )

    def test_totals_flags_none_handled(self) -> None:
        """Codecov occasionally returns ``totals.flags: null`` — coerced to empty."""
        report = {"totals": {"flags": None}}
        self.assertEqual(
            validate_codecov_flags._flags_present_in_report(report), set()
        )

    def test_flag_dict_without_name_ignored(self) -> None:
        """A flag entry with a blank ``name`` is dropped."""
        report = {"totals": {"flags": [{"name": ""}, {"name": "ok"}]}}
        self.assertEqual(
            validate_codecov_flags._flags_present_in_report(report), {"ok"}
        )

    def test_non_dict_top_level_report_yields_empty(self) -> None:
        """A non-dict report (list/None) returns empty without crashing."""
        self.assertEqual(
            validate_codecov_flags._flags_present_in_report([]),  # type: ignore[arg-type]
            set(),
        )
        self.assertEqual(
            validate_codecov_flags._flags_present_in_report(None),  # type: ignore[arg-type]
            set(),
        )


class ValidateFlagsTests(unittest.TestCase):
    """``validate_flags`` returns the declared flags missing from the report."""

    def test_all_declared_present(self) -> None:
        """Returns empty when every declared flag was reported."""
        self.assertEqual(
            validate_codecov_flags.validate_flags(
                ["a", "b"], {"a", "b", "c"}
            ),
            [],
        )

    def test_subset_missing(self) -> None:
        """Missing flags preserve declared-order for stable error messages."""
        self.assertEqual(
            validate_codecov_flags.validate_flags(
                ["first", "second", "third"], {"second"}
            ),
            ["first", "third"],
        )

    def test_empty_present(self) -> None:
        """All declared flags absent when the report is empty."""
        self.assertEqual(
            validate_codecov_flags.validate_flags(["x"], set()), ["x"]
        )


class PollForFlagsTests(unittest.TestCase):
    """``_poll_for_flags`` retries on transient errors and times out."""

    def test_returns_report_on_first_success(self) -> None:
        """The report is returned without any retries when the fetch succeeds."""
        report = {"totals": {"flags": []}}
        with patch(
            "scripts.quality.validate_codecov_flags._fetch_codecov_report",
            return_value=report,
        ) as fetch_mock:
            out = validate_codecov_flags._poll_for_flags(
                "https://api.codecov.io/api/v2/github/x/repos/y/commits/abc/",
                "",
                60,
            )
        self.assertEqual(out, report)
        self.assertEqual(fetch_mock.call_count, 1)

    def test_retries_on_404_then_succeeds(self) -> None:
        """A 404 on the first call triggers a retry; second call wins."""
        report = {"totals": {"flags": [{"name": "ok"}]}}
        http_404 = urllib.error.HTTPError(
            "u", 404, "not found", {}, None  # type: ignore[arg-type]
        )
        with patch(
            "scripts.quality.validate_codecov_flags._fetch_codecov_report",
            side_effect=[http_404, report],
        ) as fetch_mock, patch(
            "scripts.quality.validate_codecov_flags.time.sleep"
        ):
            out = validate_codecov_flags._poll_for_flags(
                "https://api.codecov.io/api/v2/github/x/repos/y/commits/abc/",
                "",
                60,
            )
        self.assertEqual(out, report)
        self.assertEqual(fetch_mock.call_count, 2)

    def test_non_retryable_http_error_reraised(self) -> None:
        """A 403 (not in the retry allowlist) propagates immediately."""
        http_403 = urllib.error.HTTPError(
            "u", 403, "forbidden", {}, None  # type: ignore[arg-type]
        )
        with patch(
            "scripts.quality.validate_codecov_flags._fetch_codecov_report",
            side_effect=http_403,
        ), patch("scripts.quality.validate_codecov_flags.time.sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                validate_codecov_flags._poll_for_flags(
                    "https://api.codecov.io/api/v2/github/x/repos/y/commits/abc/",
                    "",
                    60,
                )

    def test_url_error_triggers_retry(self) -> None:
        """Network ``URLError`` is retryable; last exception reported on timeout.

        Uses a generous deadline (60 s) so the loop actually enters the
        fetch body — the exhaustion path relies on ``RETRY_DELAYS_SECONDS``
        having only five entries, not on the wall-clock deadline.
        """
        err = urllib.error.URLError("boom")
        with patch(
            "scripts.quality.validate_codecov_flags._fetch_codecov_report",
            side_effect=err,
        ), patch("scripts.quality.validate_codecov_flags.time.sleep"):
            with self.assertRaises(TimeoutError) as ctx:
                validate_codecov_flags._poll_for_flags(
                    "https://api.codecov.io/api/v2/github/x/repos/y/commits/abc/",
                    "",
                    60,
                )
        self.assertIn("boom", str(ctx.exception))

    def test_timeout_exhausts_retry_budget(self) -> None:
        """Exhausting the deadline without success raises TimeoutError.

        A non-zero deadline ensures the first attempt runs; the loop then
        terminates either by iterating through all ``RETRY_DELAYS_SECONDS``
        or by the elapsed-wall-clock check (whichever hits first).
        """
        http_503 = urllib.error.HTTPError(
            "u", 503, "unavail", {}, None  # type: ignore[arg-type]
        )
        with patch(
            "scripts.quality.validate_codecov_flags._fetch_codecov_report",
            side_effect=http_503,
        ), patch("scripts.quality.validate_codecov_flags.time.sleep"):
            with self.assertRaises(TimeoutError) as ctx:
                validate_codecov_flags._poll_for_flags(
                    "https://api.codecov.io/api/v2/github/x/repos/y/commits/abc/",
                    "",
                    60,
                )
        self.assertIn("503", str(ctx.exception))

    def test_deadline_breach_before_any_attempt_still_raises(self) -> None:
        """``deadline_seconds=0`` causes an immediate TimeoutError."""
        with patch(
            "scripts.quality.validate_codecov_flags._fetch_codecov_report",
        ) as fetch_mock, patch(
            "scripts.quality.validate_codecov_flags.time.sleep"
        ):
            with self.assertRaises(TimeoutError):
                validate_codecov_flags._poll_for_flags(
                    "https://api.codecov.io/api/v2/github/x/repos/y/commits/abc/",
                    "",
                    0,
                )
        self.assertEqual(fetch_mock.call_count, 0)


class MainCliTests(unittest.TestCase):
    """End-to-end CLI: ``main()`` returns the documented exit codes."""

    def _write_profile(self, payload: dict) -> Path:
        """Write ``payload`` to a temp profile.json and return its path."""
        return _write_temp_json(payload, self)

    def _run_main(self, argv: list) -> int:
        """Invoke ``main()`` with ``argv`` patched onto ``sys.argv``."""
        with patch("sys.argv", ["validate_codecov_flags.py", *argv]):
            return validate_codecov_flags.main()

    def test_profile_not_found_returns_2(self) -> None:
        """Exit code 2 when the profile JSON cannot be opened."""
        rc = self._run_main([
            "--repo-slug", "Prekzursil/event-link",
            "--sha", "abc123",
            "--inputs-json", "/does/not/exist.json",
        ])
        self.assertEqual(rc, 2)

    def test_no_flags_declared_returns_0(self) -> None:
        """Empty ``inputs`` short-circuits to exit 0 before any HTTP call."""
        path = self._write_profile({"coverage": {"inputs": []}})
        rc = self._run_main([
            "--repo-slug", "Prekzursil/event-link",
            "--sha", "abc123",
            "--inputs-json", str(path),
        ])
        self.assertEqual(rc, 0)

    def test_all_flags_present_returns_0(self) -> None:
        """Exit 0 when Codecov reports every declared flag."""
        path = self._write_profile({
            "coverage": {"inputs": [
                {"name": "backend", "flag": "backend", "path": "a"},
                {"name": "ui", "flag": "ui", "path": "b"},
            ]}
        })
        report = {"totals": {"flags": [
            {"name": "backend"}, {"name": "ui"}, {"name": "extra"},
        ]}}
        with patch(
            "scripts.quality.validate_codecov_flags._poll_for_flags",
            return_value=report,
        ):
            rc = self._run_main([
                "--repo-slug", "Prekzursil/event-link",
                "--sha", "abc123",
                "--inputs-json", str(path),
            ])
        self.assertEqual(rc, 0)

    def test_missing_flag_returns_1(self) -> None:
        """Exit 1 when any declared flag is absent from the Codecov report."""
        path = self._write_profile({
            "coverage": {"inputs": [
                {"name": "backend", "flag": "backend", "path": "a"},
                {"name": "ui", "flag": "ui", "path": "b"},
            ]}
        })
        report = {"totals": {"flags": [{"name": "backend"}]}}
        with patch(
            "scripts.quality.validate_codecov_flags._poll_for_flags",
            return_value=report,
        ):
            rc = self._run_main([
                "--repo-slug", "Prekzursil/event-link",
                "--sha", "abc123",
                "--inputs-json", str(path),
            ])
        self.assertEqual(rc, 1)

    def test_timeout_returns_3(self) -> None:
        """Exit 3 when polling exceeds the deadline."""
        path = self._write_profile({
            "coverage": {"inputs": [{"name": "ui", "flag": "ui", "path": "x"}]}
        })
        with patch(
            "scripts.quality.validate_codecov_flags._poll_for_flags",
            side_effect=TimeoutError("timed out"),
        ):
            rc = self._run_main([
                "--repo-slug", "Prekzursil/event-link",
                "--sha", "abc123",
                "--inputs-json", str(path),
            ])
        self.assertEqual(rc, 3)

    def _run_with_poll_error(
        self, poll_exc: BaseException, path: Path
    ) -> int:
        """Patch ``_poll_for_flags`` to raise ``poll_exc`` and return the CLI rc.

        Factored out because the 401/403/500 scenarios share an otherwise
        identical setup — qlty flagged the three copies as duplicated code
        (mass ≥ 104).
        """
        with patch(
            "scripts.quality.validate_codecov_flags._poll_for_flags",
            side_effect=poll_exc,
        ):
            return self._run_main([
                "--repo-slug", "Prekzursil/event-link",
                "--sha", "abc123",
                "--inputs-json", str(path),
            ])

    def test_auth_http_errors_are_warn_and_skip(self) -> None:
        """Exit 0 on 401/403 — auth is a platform config issue, not a regression.

        The CODECOV_TOKEN in CI is an upload (write-scope) token. The
        Codecov v2 commit API needs a separate read-scope Bearer token,
        which most consumer repos don't have wired. Treat missing read
        auth as warn-and-skip rather than hard-fail: otherwise every
        repo without the read token has permanent red CI on a config
        problem, not a regression. 401 and 403 share this policy, so
        they share a parameterised test case.
        """
        path = self._write_profile({
            "coverage": {"inputs": [{"name": "ui", "flag": "ui", "path": "x"}]}
        })
        for code, reason in ((401, "Unauthorized"), (403, "Forbidden")):
            with self.subTest(code=code):
                exc = urllib.error.HTTPError(
                    "u", code, reason, {}, None  # type: ignore[arg-type]
                )
                self.assertEqual(self._run_with_poll_error(exc, path), 0)

    def test_http_500_still_propagates(self) -> None:
        """Non-auth HTTP errors still surface — they indicate real failures."""
        path = self._write_profile({
            "coverage": {"inputs": [{"name": "ui", "flag": "ui", "path": "x"}]}
        })
        exc = urllib.error.HTTPError(
            "u", 500, "Server Error", {}, None  # type: ignore[arg-type]
        )
        with self.assertRaises(urllib.error.HTTPError):
            self._run_with_poll_error(exc, path)


if __name__ == "__main__":
    unittest.main()
