"""Tests for the Codacy quality-threshold lookup (``check_codacy_zero`` + ``codacy_quality_thresholds``).

The status tests mock ``_quality_threshold_findings`` wholesale; these pin what
it does: try each provider alias, treat a 404 as "try the next alias", and turn
every other failure into one fetch-failed finding, never a silent pass.
"""

from __future__ import absolute_import

import io
import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch

from scripts.quality import check_codacy_zero as codacy
from scripts.quality import codacy_quality_thresholds as thresholds

QUERY = codacy.CodacyQuery(provider="gh", owner="Prekzursil", repo="demo")


def _http_error(code: int) -> urllib.error.HTTPError:
    """An HTTPError as urllib raises it."""
    return urllib.error.HTTPError("https://api.codacy.com/x", code, "boom", Message(), io.BytesIO(b""))


class RequestJsonTests(unittest.TestCase):
    """The Codacy API helper sends JSON bodies as JSON."""

    def test_a_json_body_is_sent_with_its_content_type(self) -> None:
        """POST data is encoded and labelled; the trailing slash is dropped."""
        with patch.object(codacy, "load_json_https", return_value=({"ok": 1}, {})) as fetch:
            payload = codacy._request_json("https://api.codacy.com/x/", "tok", method="POST", data={"a": 1})
        self.assertEqual(payload, {"ok": 1})
        self.assertEqual(fetch.call_args.args, ("https://api.codacy.com/x",))
        kwargs = fetch.call_args.kwargs
        self.assertEqual((kwargs["method"], kwargs["data"]), ("POST", b'{"a": 1}'))
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(kwargs["headers"]["api-token"], "tok")
        self.assertEqual(kwargs["allowed_host_suffixes"], {"codacy.com"})


class ThresholdsForOneProviderTests(unittest.TestCase):
    """One provider alias: a snapshot is evaluated, a 404 is recoverable, anything else is a finding."""

    def test_a_snapshot_is_evaluated_at_the_strict_zero_floor(self) -> None:
        """The repository-analysis URL is fetched through the gate's own JSON helper."""
        with (
            patch.object(codacy, "fetch_repository_quality", return_value="snapshot") as fetch,
            patch.object(codacy, "evaluate_quality_thresholds", return_value=["duplication drift"]) as evaluate,
        ):
            result = codacy._quality_threshold_findings_for_provider(QUERY, "tok", "gh")
        self.assertEqual(result, (["duplication drift"], None))
        self.assertEqual(
            fetch.call_args.args, (codacy.build_repository_analysis_url("gh", "Prekzursil", "demo"), "tok")
        )
        self.assertIs(fetch.call_args.kwargs["request_json"], codacy._request_json)
        evaluate.assert_called_once_with("snapshot", coverage_floor=codacy.DEFAULT_COVERAGE_FLOOR)

    def test_a_404_is_recoverable(self) -> None:
        """No findings, but the exception is handed back so the caller can try the next alias."""
        not_found = _http_error(404)
        with patch.object(codacy, "fetch_repository_quality", side_effect=not_found):
            self.assertEqual(codacy._quality_threshold_findings_for_provider(QUERY, "t", "gh"), ([], not_found))

    def test_every_other_failure_is_a_fetch_failed_finding(self) -> None:
        """HTTP 5xx, socket, shape and parse errors all surface as one finding."""
        for error in (_http_error(500), OSError("dns"), RuntimeError("shape"), ValueError("json")):
            with self.subTest(error=repr(error)), patch.object(codacy, "fetch_repository_quality", side_effect=error):
                findings, exc = codacy._quality_threshold_findings_for_provider(QUERY, "t", "gh")
                self.assertIs(exc, error)
                self.assertEqual(findings, [f"Codacy quality-threshold fetch failed: {error}"])


class ThresholdsAcrossProvidersTests(unittest.TestCase):
    """Aliases are tried in order; the first real answer wins."""

    def _lookup(self, outcomes, providers):
        with patch.object(
            codacy, "_quality_threshold_findings_for_provider", side_effect=lambda q, t, p: outcomes[p]
        ) as one:
            findings = codacy._quality_threshold_findings(QUERY, "t", provider_candidates=providers)
        return findings, [call.args[2] for call in one.call_args_list]

    def test_a_404_falls_through_to_the_next_alias(self) -> None:
        """`gh` missing, `github` answers."""
        findings, tried = self._lookup(
            {"gh": ([], _http_error(404)), "github": (["coverage 99%"], None)}, ["gh", "github"]
        )
        self.assertEqual((findings, tried), (["coverage 99%"], ["gh", "github"]))

    def test_a_hard_failure_stops_at_the_first_alias(self) -> None:
        """A non-404 failure is reported at once, not masked by the next alias."""
        failure = (["Codacy quality-threshold fetch failed: x"], RuntimeError("x"))
        findings, tried = self._lookup({"gh": failure, "github": (["never"], None)}, ["gh", "github"])
        self.assertEqual((findings, tried), (failure[0], ["gh"]))

    def test_every_alias_missing_is_one_fetch_failed_finding(self) -> None:
        """All 404s report the last one; no aliases at all is no finding."""
        last = _http_error(404)
        findings, _ = self._lookup({"gh": ([], _http_error(404)), "github": ([], last)}, ["gh", "github"])
        self.assertEqual(findings, [f"Codacy quality-threshold fetch failed: {last}"])
        self.assertEqual(self._lookup({}, [])[0], [])


class SnapshotEdgeTests(unittest.TestCase):
    """Malformed payloads parse to "unknown", and an unknown percentage is never a pass."""

    def test_non_numeric_values_and_a_non_mapping_data_block_are_unknown(self) -> None:
        """Strings and booleans are not numbers; a list `data` block is ignored."""
        self.assertIsNone(thresholds._coerce_number("97"))
        self.assertIsNone(thresholds._coerce_number(True))
        snapshot = thresholds.parse_quality_snapshot({"data": ["not", "a", "mapping"]})
        self.assertIsNone(snapshot.issues_percentage)
        self.assertFalse(snapshot.coverage_uploaded)

    def test_an_uploaded_coverage_without_a_percentage_is_missing(self) -> None:
        """Defensive: a snapshot claiming an upload but no figure fails the strict-zero floor."""
        snapshot = thresholds.CodacyQualitySnapshot(
            issues_percentage=None,
            complex_files_percentage=None,
            duplication_percentage=None,
            coverage_percentage=None,
            coverage_uploaded=True,
            max_issue_percentage=None,
            max_complex_files_percentage=None,
            max_duplicated_files_percentage=None,
            min_coverage_percentage=None,
        )
        findings = thresholds._coverage_findings(snapshot, coverage_floor=100)
        self.assertEqual(len(findings), 1)
        self.assertIn("omitted a percentage value", findings[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
