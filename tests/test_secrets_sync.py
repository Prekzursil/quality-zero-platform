"""Tests for ``scripts.quality.secrets_sync`` — secret propagation + audit.

Covers the §9 secrets-sync invariants:

* Every sync emits one JSONL audit record per repo it touched.
* Audit records NEVER include the secret value — only the name +
  destination slug + timestamp.
* Dry-run never invokes the gh runner.
"""

from __future__ import absolute_import

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from scripts.quality import secrets_sync


def _fake_completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    """CompletedProcess double for runner mocks."""
    return subprocess.CompletedProcess(
        args=["gh"], returncode=returncode, stdout=stdout, stderr="",
    )


class SyncSecretTests(unittest.TestCase):
    """``sync_secret`` propagates one secret via an injected closure."""

    def test_dry_run_does_not_invoke_setter(self) -> None:
        """``dry_run=True`` yields audit records without calling the setter."""
        setter = MagicMock()
        records = secrets_sync.sync_secret(
            secret_name="CODECOV_TOKEN",
            target_slugs=["org/a", "org/b"],
            secret_setter=setter,
            dry_run=True,
        )
        setter.assert_not_called()
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(record["status"], "dry-run")

    def test_missing_setter_treated_as_dry_run(self) -> None:
        """``secret_setter=None`` without dry_run → records are dry-run only."""
        records = secrets_sync.sync_secret(
            secret_name="X",
            target_slugs=["org/a"],
        )
        self.assertEqual(records[0]["status"], "dry-run")

    def test_records_never_contain_secret_value_via_closure(self) -> None:
        """Audit records NEVER carry the secret value — just the name."""
        # The closure captures the secret; sync_secret never sees it.
        runner = MagicMock(return_value=_fake_completed(""))
        setter = secrets_sync.make_gh_secret_setter(
            "super-sensitive-token-abc123", runner=runner,
        )
        records = secrets_sync.sync_secret(
            secret_name="SONAR_TOKEN",
            target_slugs=["org/repo"],
            secret_setter=setter,
        )
        for record in records:
            with self.subTest(record=record):
                self.assertNotIn("secret_value", record)
                for value in record.values():
                    self.assertNotIn(
                        "super-sensitive-token-abc123", str(value),
                    )

    def test_setter_called_once_per_target(self) -> None:
        """Each target triggers exactly one setter call."""
        setter = MagicMock(return_value=_fake_completed(""))
        records = secrets_sync.sync_secret(
            secret_name="CODECOV_TOKEN",
            target_slugs=["org/a", "org/b", "org/c"],
            secret_setter=setter,
        )
        self.assertEqual(setter.call_count, 3)
        self.assertEqual(len(records), 3)
        for record in records:
            self.assertEqual(record["status"], "synced")

    def test_setter_failure_marks_record_as_failed(self) -> None:
        """Non-zero returncode from the setter → status=failed."""
        setter = MagicMock(return_value=_fake_completed("", returncode=1))
        records = secrets_sync.sync_secret(
            secret_name="CODECOV_TOKEN",
            target_slugs=["org/locked-down"],
            secret_setter=setter,
        )
        self.assertEqual(records[0]["status"], "failed")

    def test_empty_target_list_returns_empty(self) -> None:
        """No targets → no records, no setter calls."""
        setter = MagicMock()
        records = secrets_sync.sync_secret(
            secret_name="X",
            target_slugs=[],
            secret_setter=setter,
        )
        self.assertEqual(records, [])
        setter.assert_not_called()

    def test_blank_slugs_are_skipped(self) -> None:
        """Whitespace-only target slugs are dropped, no setter call for them."""
        setter = MagicMock(return_value=_fake_completed(""))
        records = secrets_sync.sync_secret(
            secret_name="X",
            target_slugs=["", "  ", "org/real"],
            secret_setter=setter,
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["target_slug"], "org/real")
        self.assertEqual(setter.call_count, 1)


class MakeGhSecretSetterTests(unittest.TestCase):
    """``make_gh_secret_setter`` returns a closure that calls gh."""

    def test_closure_invokes_gh_secret_set_with_runner(self) -> None:
        """Calling the closure → exactly one ``gh secret set`` invocation."""
        runner = MagicMock(return_value=_fake_completed(""))
        setter = secrets_sync.make_gh_secret_setter("tok-value", runner=runner)
        setter("CODECOV_TOKEN", "org/a")
        self.assertEqual(runner.call_count, 1)
        argv = runner.call_args.args[0]
        self.assertEqual(argv[:3], ["gh", "secret", "set"])
        self.assertIn("CODECOV_TOKEN", argv)
        self.assertIn("--repo", argv)
        self.assertIn("org/a", argv)


class AppendAuditJsonlTests(unittest.TestCase):
    """``append_audit_jsonl`` writes one canonical record per line."""

    def test_appends_records_with_sort_keys(self) -> None:
        """Records are one-line, sort_keys=True, no spaces, newline-terminated."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets-sync.jsonl"
            secrets_sync.append_audit_jsonl(path, [
                {
                    "target_slug": "org/b", "secret_name": "A",
                    "status": "synced", "timestamp_utc": "2026-04-23T12:00:00Z",
                },
                {
                    "target_slug": "org/a", "secret_name": "B",
                    "status": "synced", "timestamp_utc": "2026-04-23T12:01:00Z",
                },
            ])
            content = path.read_text(encoding="utf-8")
        lines = content.splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            parsed = json.loads(line)
            self.assertIn("target_slug", parsed)
            self.assertIn("secret_name", parsed)
            self.assertIn("timestamp_utc", parsed)
            # Keys should come out in sorted order so greps are stable:
            key_order = [k for k in parsed]
            self.assertEqual(key_order, sorted(key_order))

    def test_appends_to_existing_file(self) -> None:
        """Second call appends, doesn't truncate."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets-sync.jsonl"
            secrets_sync.append_audit_jsonl(path, [
                {"target_slug": "org/a", "secret_name": "X", "status": "synced"},
            ])
            secrets_sync.append_audit_jsonl(path, [
                {"target_slug": "org/b", "secret_name": "Y", "status": "synced"},
            ])
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)

    def test_creates_parent_directories(self) -> None:
        """``append_audit_jsonl`` creates missing parents automatically."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit" / "nested" / "secrets-sync.jsonl"
            secrets_sync.append_audit_jsonl(path, [
                {"target_slug": "org/a", "secret_name": "X", "status": "synced"},
            ])
            self.assertTrue(path.is_file())

    def test_sanitiser_drops_unknown_keys(self) -> None:
        """Any non-whitelisted key (including ``secret_value``) is dropped."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets-sync.jsonl"
            # A misuse that includes secret_value in the record would NOT
            # survive to the JSONL thanks to the sanitiser.
            secrets_sync.append_audit_jsonl(path, [
                {
                    "target_slug": "org/a", "secret_name": "X",
                    "status": "synced",
                    "secret_value": "should-never-land-in-jsonl",
                    "extra_junk": "also-dropped",
                },
            ])
            line = path.read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        self.assertNotIn("secret_value", parsed)
        self.assertNotIn("extra_junk", parsed)
        self.assertNotIn("should-never-land-in-jsonl", line)


class TimestampTests(unittest.TestCase):
    """Records carry a UTC ISO-8601 timestamp."""

    def test_timestamp_is_iso8601_utc(self) -> None:
        """``timestamp_utc`` ends with Z (UTC marker)."""
        records = secrets_sync.sync_secret(
            secret_name="X",
            target_slugs=["org/a"],
            dry_run=True,
        )
        self.assertTrue(records[0]["timestamp_utc"].endswith("Z"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
