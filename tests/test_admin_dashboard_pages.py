"""Tests for Phase 5 ``scripts.quality.admin_dashboard_pages``.

Renders 3 dashboard pages beyond the existing ``index.html`` repo
heatmap:

* ``coverage.html`` — per-repo coverage trend.
* ``drift.html`` — open/closed drift-sync PR list.
* ``audit.html`` — break-glass / skip JSONL feed.

Also exercises the private-repo redaction helper.
"""

from __future__ import absolute_import

import json
import tempfile
import unittest
from pathlib import Path

from scripts.quality import admin_dashboard_pages as pages


class RedactPrivateRepoRowsTests(unittest.TestCase):
    """Private repo rows are masked in every page."""

    def test_public_rows_survive_unchanged(self) -> None:
        """``visibility == "public"`` rows are returned as-is."""
        rows = [
            {"slug": "org/public-a", "visibility": "public", "cov": 100.0},
            {"slug": "org/public-b", "visibility": "public", "cov": 95.0},
        ]
        out = pages.redact_private_repos(rows)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["slug"], "org/public-a")

    def test_private_rows_are_masked(self) -> None:
        """Private slugs → ``<private>``, numeric metrics preserved."""
        rows = [
            {"slug": "org/secret", "visibility": "private", "cov": 100.0},
        ]
        out = pages.redact_private_repos(rows)
        self.assertEqual(out[0]["slug"], "<private>")
        self.assertEqual(out[0]["cov"], 100.0)

    def test_missing_visibility_treated_as_public(self) -> None:
        """Rows without ``visibility`` default to public (opt-out model)."""
        rows = [{"slug": "org/repo", "cov": 100.0}]
        out = pages.redact_private_repos(rows)
        self.assertEqual(out[0]["slug"], "org/repo")


class RenderCoverageTrendPageTests(unittest.TestCase):
    """``render_coverage_trend_page`` emits an HTML trend table."""

    def test_renders_each_repo_as_a_row(self) -> None:
        """One <tr> per repo with slug + coverage value."""
        html = pages.render_coverage_trend_page(rows=[
            {"slug": "org/repo-a", "coverage_percent": 100.0},
            {"slug": "org/repo-b", "coverage_percent": 96.3},
        ])
        self.assertIn("<title>Coverage", html)
        self.assertIn("org/repo-a", html)
        self.assertIn("100.0", html)
        self.assertIn("org/repo-b", html)
        self.assertIn("96.3", html)

    def test_empty_rows_renders_placeholder(self) -> None:
        """No rows → friendly placeholder in body."""
        html = pages.render_coverage_trend_page(rows=[])
        self.assertIn("No coverage data", html)


class RenderDriftPageTests(unittest.TestCase):
    """``render_drift_page`` emits the drift-sync PR list."""

    def test_renders_each_entry_status_and_sync_pr(self) -> None:
        """Each drift entry shows slug + status + PR url."""
        html = pages.render_drift_page(entries=[
            {
                "slug": "org/repo-a",
                "status": "open",
                "pr_url": "https://github.com/org/repo-a/pull/42",
            },
            {"slug": "org/repo-b", "status": "closed", "pr_url": ""},
        ])
        self.assertIn("org/repo-a", html)
        self.assertIn("open", html)
        self.assertIn("pull/42", html)
        self.assertIn("closed", html)

    def test_empty_entries_renders_placeholder(self) -> None:
        """No drift → placeholder."""
        html = pages.render_drift_page(entries=[])
        self.assertIn("No drift", html)


class RenderAuditPageTests(unittest.TestCase):
    """``render_audit_page`` emits the bypass JSONL feed."""

    def test_renders_each_audit_row(self) -> None:
        """Each audit row shows timestamp + label + pr_slug + actor."""
        html = pages.render_audit_page(entries=[
            {
                "timestamp": "2026-04-23T12:00:00Z",
                "label": "quality-zero:break-glass",
                "pr_slug": "org/repo",
                "pr_number": 42,
                "actor": "alice",
                "incident": "INC-1234",
            },
            {
                "timestamp": "2026-04-23T13:00:00Z",
                "label": "quality-zero:skip",
                "pr_slug": "org/repo",
                "pr_number": 43,
                "actor": "bob",
            },
        ])
        self.assertIn("break-glass", html)
        self.assertIn("INC-1234", html)
        self.assertIn("alice", html)
        self.assertIn("bob", html)

    def test_empty_entries_renders_placeholder(self) -> None:
        """No bypass events → placeholder."""
        html = pages.render_audit_page(entries=[])
        self.assertIn("No bypass", html)


class LoadAuditJsonlTests(unittest.TestCase):
    """``load_audit_jsonl`` reads a JSONL bypass audit file."""

    def test_valid_jsonl_round_trips(self) -> None:
        """Every one-line record is returned as a dict."""
        records = [
            {"timestamp": "2026-04-23T12:00:00Z", "label": "quality-zero:skip",
             "pr_slug": "org/repo", "pr_number": 1, "actor": "alice"},
            {"timestamp": "2026-04-23T13:00:00Z", "label": "quality-zero:break-glass",
             "pr_slug": "org/repo", "pr_number": 2, "actor": "bob", "incident": "INC-99"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "break-glass.jsonl"
            path.write_text(
                "\n".join(json.dumps(r, sort_keys=True) for r in records) + "\n",
                encoding="utf-8",
            )
            loaded = pages.load_audit_jsonl(path)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["actor"], "alice")
        self.assertEqual(loaded[1]["incident"], "INC-99")

    def test_missing_file_returns_empty_list(self) -> None:
        """Absent file → ``[]`` (not an error)."""
        loaded = pages.load_audit_jsonl(Path("/does/not/exist.jsonl"))
        self.assertEqual(loaded, [])

    def test_blank_lines_skipped(self) -> None:
        """Blank / whitespace-only lines are skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            path.write_text(
                "\n\n"
                + json.dumps({"label": "x"}, sort_keys=True) + "\n"
                + "  \n",
                encoding="utf-8",
            )
            loaded = pages.load_audit_jsonl(path)
        self.assertEqual(len(loaded), 1)

    def test_non_dict_lines_filtered_out(self) -> None:
        """Lines that parse to non-dict JSON (e.g. array) are skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            path.write_text(
                json.dumps([1, 2, 3]) + "\n"
                + json.dumps({"label": "real"}) + "\n",
                encoding="utf-8",
            )
            loaded = pages.load_audit_jsonl(path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["label"], "real")


class LoadCoverageRowsTests(unittest.TestCase):
    """``load_coverage_rows`` parses per-repo coverage state JSON."""

    def test_direct_list_returns_rows(self) -> None:
        """File containing a bare JSON list of dicts → rows returned as-is."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "coverage.json"
            path.write_text(json.dumps([
                {"slug": "org/a", "coverage_percent": 100.0},
                {"slug": "org/b", "coverage_percent": 98.5},
            ]), encoding="utf-8")
            rows = pages.load_coverage_rows(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["slug"], "org/a")

    def test_object_with_repos_key_returns_inner_list(self) -> None:
        """``{"repos": [...]}`` wrapper (matches inventory shape) also works."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "coverage.json"
            path.write_text(json.dumps({"repos": [
                {"slug": "org/a", "coverage_percent": 100.0},
            ]}), encoding="utf-8")
            rows = pages.load_coverage_rows(path)
        self.assertEqual(len(rows), 1)

    def test_missing_file_returns_empty(self) -> None:
        """Absent path → empty list, no exception."""
        rows = pages.load_coverage_rows(Path("/does/not/exist.json"))
        self.assertEqual(rows, [])

    def test_non_dict_rows_filtered(self) -> None:
        """Rows that aren't dicts (stray scalars) are dropped."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "coverage.json"
            path.write_text(json.dumps([
                "not-a-dict",
                {"slug": "org/a", "coverage_percent": 100.0},
            ]), encoding="utf-8")
            rows = pages.load_coverage_rows(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["slug"], "org/a")

    def test_unexpected_root_shape_returns_empty(self) -> None:
        """Root scalar (or dict without 'repos') → empty list, no exception."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "coverage.json"
            path.write_text(json.dumps("just-a-string"), encoding="utf-8")
            rows = pages.load_coverage_rows(path)
        self.assertEqual(rows, [])


class LoadDriftEntriesTests(unittest.TestCase):
    """``load_drift_entries`` parses drift-sync JSONL."""

    def test_loads_each_line_as_dict(self) -> None:
        """One JSONL line per entry."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "drift.jsonl"
            path.write_text(
                json.dumps({"slug": "org/a", "status": "open"}) + "\n"
                + json.dumps({"slug": "org/b", "status": "closed"}) + "\n",
                encoding="utf-8",
            )
            entries = pages.load_drift_entries(path)
        self.assertEqual(len(entries), 2)

    def test_missing_file_returns_empty(self) -> None:
        """Absent drift file → empty list."""
        entries = pages.load_drift_entries(Path("/nope.jsonl"))
        self.assertEqual(entries, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
