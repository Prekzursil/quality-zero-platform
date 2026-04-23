"""Tests for ``scripts.quality.bypass_labels`` — Phase 4 §5.2 handlers."""

from __future__ import absolute_import

import json
import tempfile
import unittest
from pathlib import Path

from scripts.quality import bypass_labels as bl


class ExtractIncidentTests(unittest.TestCase):
    """``extract_incident_id`` parses the ``Incident: <id>`` line."""

    def test_typical_incident_line_is_matched(self) -> None:
        """Standard INC-1234 format is recognised."""
        self.assertEqual(
            bl.extract_incident_id("Incident: INC-1234"),
            "INC-1234",
        )

    def test_match_is_case_insensitive(self) -> None:
        """``incident:`` and ``INCIDENT:`` are both accepted."""
        self.assertEqual(
            bl.extract_incident_id("incident: pd-42"),
            "pd-42",
        )

    def test_whitespace_tolerated(self) -> None:
        """Extra spaces before/after the colon don't break the match."""
        self.assertEqual(
            bl.extract_incident_id("Incident :  INC-9"),
            "INC-9",
        )

    def test_namespaced_incident_id_allowed(self) -> None:
        """Slash-delimited providers like ``pagerduty/INC-1234`` work."""
        self.assertEqual(
            bl.extract_incident_id("Incident: pagerduty/INC-1234"),
            "pagerduty/INC-1234",
        )

    def test_multi_line_body_picks_first(self) -> None:
        """Two incident lines → the first one wins."""
        body = "Context line\nIncident: INC-1\nOther: junk\nIncident: INC-2\n"
        self.assertEqual(bl.extract_incident_id(body), "INC-1")

    def test_no_incident_line_returns_none(self) -> None:
        """Body without an incident line returns ``None``."""
        self.assertIsNone(bl.extract_incident_id("No incident here."))

    def test_non_string_body_returns_none(self) -> None:
        """Defensive: non-string input still returns ``None``."""
        self.assertIsNone(bl.extract_incident_id(None))  # type: ignore[arg-type]
        self.assertIsNone(bl.extract_incident_id(42))  # type: ignore[arg-type]


class EvaluateBreakGlassTests(unittest.TestCase):
    """``evaluate_break_glass`` enforces the Incident-id requirement."""

    def test_missing_incident_raises(self) -> None:
        """Break-glass without Incident: raises BypassError."""
        with self.assertRaises(bl.BypassError):
            bl.evaluate_break_glass(
                pr_body="forgot the incident id",
                pr_slug="owner/repo",
                pr_number=42,
                head_sha="abc123",
                actor="alice",
            )

    def test_valid_incident_returns_decision(self) -> None:
        """With an Incident line the handler returns an allowed decision."""
        decision = bl.evaluate_break_glass(
            pr_body="Reason: urgent fix\nIncident: INC-1234",
            pr_slug="owner/repo",
            pr_number=42,
            head_sha="a" * 40,
            actor="alice",
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.incident, "INC-1234")
        self.assertIsNotNone(decision.audit_record)
        self.assertEqual(
            decision.audit_record["pr"]["slug"], "owner/repo"
        )
        self.assertEqual(decision.audit_record["actor"], "alice")
        self.assertEqual(decision.label, bl.BREAK_GLASS_LABEL)

    def test_audit_record_contains_timestamp(self) -> None:
        """Each audit record is timestamped in ISO UTC."""
        decision = bl.evaluate_break_glass(
            pr_body="Incident: INC-1",
            pr_slug="x/y",
            pr_number=1,
            head_sha="s",
            actor="a",
        )
        ts = decision.audit_record["timestamp_utc"]
        self.assertTrue(ts.endswith("Z"))
        # Parseable back into a datetime.
        from datetime import datetime

        datetime.fromisoformat(ts.replace("Z", "+00:00"))

    def test_tracking_issue_body_references_pr_and_actor(self) -> None:
        """Tracking issue body names the PR, the actor, and the incident."""
        decision = bl.evaluate_break_glass(
            pr_body="Incident: INC-99",
            pr_slug="Prekzursil/event-link",
            pr_number=77,
            head_sha="s",
            actor="bob",
        )
        title = decision.tracking_issue_title
        body = decision.tracking_issue_body
        self.assertIn("Prekzursil/event-link", title)
        self.assertIn("#77", title)
        self.assertIn("INC-99", title)
        self.assertIn("@bob", body)
        self.assertIn("Post-merge remediation", body)


class EvaluateSkipTests(unittest.TestCase):
    """``evaluate_skip`` always allows but still audits."""

    def test_skip_always_allowed_without_incident(self) -> None:
        """No Incident line is needed for the skip label."""
        decision = bl.evaluate_skip(
            pr_body="",  # no incident
            pr_slug="x/y",
            pr_number=5,
            head_sha="s",
            actor="alice",
        )
        self.assertTrue(decision.allowed)
        self.assertIsNone(decision.incident)
        self.assertIsNotNone(decision.audit_record)
        self.assertEqual(decision.label, bl.SKIP_LABEL)

    def test_skip_has_no_tracking_issue(self) -> None:
        """Skip label doesn't require the post-merge follow-up issue."""
        decision = bl.evaluate_skip(
            pr_body="", pr_slug="x/y", pr_number=5, head_sha="s", actor="a",
        )
        self.assertIsNone(decision.tracking_issue_title)
        self.assertIsNone(decision.tracking_issue_body)

    def test_skip_audit_record_excludes_incident_key(self) -> None:
        """Skip records don't include an incident field at all."""
        decision = bl.evaluate_skip(
            pr_body="Incident: INC-x",  # ignored
            pr_slug="x/y", pr_number=5, head_sha="s", actor="a",
        )
        self.assertNotIn("incident", decision.audit_record)


class AppendJsonlTests(unittest.TestCase):
    """``append_jsonl`` writes one compact line per record."""

    def test_creates_parent_dir_and_appends_line(self) -> None:
        """First call creates the directory + file."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "audit.jsonl"
            bl.append_jsonl(target, {"a": 1})
            bl.append_jsonl(target, {"b": 2})
            lines = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0]), {"a": 1})
        self.assertEqual(json.loads(lines[1]), {"b": 2})

    def test_records_are_sorted_keys(self) -> None:
        """``sort_keys=True`` makes the jsonl diff-friendly."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "audit.jsonl"
            bl.append_jsonl(target, {"z": 1, "a": 2})
            line = target.read_text(encoding="utf-8").strip()
        self.assertEqual(line, '{"a":2,"z":1}')


class IntegrationFlowTests(unittest.TestCase):
    """End-to-end: evaluate + append → file on disk has the expected line."""

    def test_break_glass_flow_writes_audit_line(self) -> None:
        """Break-glass happy path ends in one audit line on disk."""
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "audit" / "break-glass.jsonl"
            decision = bl.evaluate_break_glass(
                pr_body="Incident: INC-42",
                pr_slug="x/y", pr_number=1, head_sha="s", actor="a",
            )
            bl.append_jsonl(audit, decision.audit_record)
            line = audit.read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        self.assertEqual(parsed["incident"], "INC-42")
        self.assertEqual(parsed["label"], bl.BREAK_GLASS_LABEL)
        self.assertEqual(parsed["pr"]["number"], 1)

    def test_skip_flow_writes_audit_line(self) -> None:
        """Skip happy path ends in one audit line on disk."""
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "audit" / "skip.jsonl"
            decision = bl.evaluate_skip(
                pr_body="", pr_slug="x/y",
                pr_number=2, head_sha="s", actor="a",
            )
            bl.append_jsonl(audit, decision.audit_record)
            line = audit.read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        self.assertEqual(parsed["label"], bl.SKIP_LABEL)
        self.assertNotIn("incident", parsed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
