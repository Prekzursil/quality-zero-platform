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
    """``sync_secret`` propagates one secret to N target repos."""

    def test_dry_run_does_not_invoke_runner(self) -> None:
        """``dry_run=True`` yields audit records without touching gh."""
        runner = MagicMock()
        records = secrets_sync.sync_secret(
            secret_name="CODECOV_TOKEN",
            secret_value="tok-dont-log-me",
            target_slugs=["org/a", "org/b"],
            runner=runner,
            dry_run=True,
        )
        runner.assert_not_called()
        self.assertEqual(len(records), 2)
        self.assertEqual(
            sorted(r["target_slug"] for r in records),
            ["org/a", "org/b"],
        )

    def test_records_never_contain_secret_value(self) -> None:
        """Audit records NEVER carry the secret value — just the name."""
        runner = MagicMock()
        records = secrets_sync.sync_secret(
            secret_name="SONAR_TOKEN",
            secret_value="super-sensitive-token-abc123",
            target_slugs=["org/repo"],
            runner=runner,
            dry_run=True,
        )
        for record in records:
            with self.subTest(record=record):
                self.assertNotIn("secret_value", record)
                for value in record.values():
                    self.assertNotIn(
                        "super-sensitive-token-abc123", str(value),
                    )

    def test_real_call_invokes_gh_secret_set_per_target(self) -> None:
        """For N target repos → N ``gh secret set`` invocations."""
        runner = MagicMock(return_value=_fake_completed(""))
        records = secrets_sync.sync_secret(
            secret_name="CODECOV_TOKEN",
            secret_value="tok-value",
            target_slugs=["org/a", "org/b", "org/c"],
            runner=runner,
        )
        self.assertEqual(runner.call_count, 3)
        for call in runner.call_args_list:
            argv = call.args[0]
            self.assertEqual(argv[0], "gh")
            self.assertIn("secret", argv)
            self.assertIn("set", argv)
            self.assertIn("CODECOV_TOKEN", argv)
            self.assertIn("--repo", argv)
        self.assertEqual(len(records), 3)
        for record in records:
            self.assertEqual(record["status"], "synced")

    def test_runner_failure_marks_record_as_failed(self) -> None:
        """Non-zero gh exit → record shows status=failed with stderr."""
        runner = MagicMock(return_value=_fake_completed(
            stdout="", returncode=1,
        ))
        records = secrets_sync.sync_secret(
            secret_name="CODECOV_TOKEN",
            secret_value="tok",
            target_slugs=["org/locked-down"],
            runner=runner,
        )
        self.assertEqual(records[0]["status"], "failed")

    def test_empty_target_list_returns_empty(self) -> None:
        """No targets → no records, no runner calls."""
        runner = MagicMock()
        records = secrets_sync.sync_secret(
            secret_name="X",
            secret_value="v",
            target_slugs=[],
            runner=runner,
        )
        self.assertEqual(records, [])
        runner.assert_not_called()

    def test_blank_slugs_are_skipped(self) -> None:
        """Whitespace-only target slugs are dropped, no runner call for them."""
        runner = MagicMock(return_value=_fake_completed(""))
        records = secrets_sync.sync_secret(
            secret_name="X",
            secret_value="v",
            target_slugs=["", "  ", "org/real"],
            runner=runner,
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["target_slug"], "org/real")
        self.assertEqual(runner.call_count, 1)


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


class TimestampTests(unittest.TestCase):
    """Records carry a UTC ISO-8601 timestamp."""

    def test_timestamp_is_iso8601_utc(self) -> None:
        """``timestamp_utc`` ends with Z (UTC marker)."""
        records = secrets_sync.sync_secret(
            secret_name="X",
            secret_value="v",
            target_slugs=["org/a"],
            runner=MagicMock(),
            dry_run=True,
        )
        self.assertTrue(records[0]["timestamp_utc"].endswith("Z"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
