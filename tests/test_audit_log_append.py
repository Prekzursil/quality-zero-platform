"""The bypass audit record is appended through the Contents API, never lost on a race."""

from __future__ import absolute_import

import base64
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, List, Optional

from scripts.quality import audit_log_append as audit

RECORD = '{"label": "quality-zero:skip", "pr": {"slug": "o/r", "number": 5}}'


class FakeGitHub:
    """A Contents API that can lose a race: ``conflicts`` PUTs are answered with a 409 first."""

    def __init__(self, content: Optional[str], *, conflicts: int = 0, read_error: str = "", write_error: str = ""):
        self.content = content
        self.sha = 1
        self.conflicts = conflicts
        self.read_error = read_error
        self.write_error = write_error
        self.puts: List[dict] = []

    def __call__(self, argv: List[str], **kwargs: Any) -> "subprocess.CompletedProcess[str]":
        if argv[:3] == ["gh", "api", "-X"]:
            body = json.loads(kwargs["input"])
            self.puts.append(body)
            if self.write_error:
                return subprocess.CompletedProcess(argv, 1, "", self.write_error)
            if self.conflicts:
                self.conflicts -= 1
                # Another writer got there first: the content moved on.
                self.content = (self.content or "") + '{"other": true}\n'
                self.sha += 1
                return subprocess.CompletedProcess(argv, 1, "", "gh: is at 2 but expected 1 (HTTP 409)")
            if body.get("sha") != (str(self.sha) if self.content is not None else None):
                return subprocess.CompletedProcess(argv, 1, "", "sha does not match (HTTP 409)")
            self.content = base64.b64decode(body["content"]).decode("utf-8")
            self.sha += 1
            return subprocess.CompletedProcess(argv, 0, "{}", "")
        if self.read_error:
            return subprocess.CompletedProcess(argv, 1, "", self.read_error)
        if self.content is None:
            return subprocess.CompletedProcess(argv, 1, "", "gh: Not Found (HTTP 404)")
        payload = {"content": base64.b64encode(self.content.encode("utf-8")).decode("ascii"), "sha": str(self.sha)}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")


def _append(github: FakeGitHub) -> int:
    return audit.append_record("o/qzp", "audit/skip.jsonl", RECORD, branch="main", message="m", runner=github)


class AppendRecordTests(unittest.TestCase):
    """Create, append, and survive a concurrent writer."""

    def test_the_first_record_creates_the_log(self) -> None:
        """A 404 means no log yet: PUT without a sha creates it."""
        github = FakeGitHub(None)
        self.assertEqual(_append(github), 1)
        self.assertEqual(github.content, RECORD + "\n")
        self.assertNotIn("sha", github.puts[0])
        self.assertEqual(github.puts[0]["branch"], "main")

    def test_a_record_is_appended_after_the_current_content(self) -> None:
        """Existing lines stay; a missing final newline is repaired first."""
        github = FakeGitHub('{"first": 1}')
        _append(github)
        self.assertEqual(github.content, '{"first": 1}\n' + RECORD + "\n")

    def test_a_lost_race_retries_from_the_new_content(self) -> None:
        """THE race: the other writer's line survives and ours lands after it, once."""
        github = FakeGitHub('{"first": 1}\n', conflicts=1)
        self.assertEqual(_append(github), 2)
        self.assertEqual(github.content, '{"first": 1}\n{"other": true}\n' + RECORD + "\n")

    def test_a_log_that_keeps_moving_is_an_error_not_a_dropped_record(self) -> None:
        """Three lost races in a row fail loudly."""
        with self.assertRaisesRegex(audit.AuditAppendError, "kept changing underneath"):
            _append(FakeGitHub("", conflicts=3))

    def test_read_and_write_failures_raise(self) -> None:
        """Anything but 404 on read, or 409 on write, is an error."""
        with self.assertRaisesRegex(audit.AuditAppendError, "reading audit/skip.jsonl failed: boom"):
            _append(FakeGitHub("", read_error="boom (HTTP 403)"))
        with self.assertRaisesRegex(audit.AuditAppendError, "writing audit/skip.jsonl failed: denied"):
            _append(FakeGitHub("", write_error="denied (HTTP 403)"))

    def test_a_malformed_record_never_reaches_the_log(self) -> None:
        """The record must be JSON; nothing is written otherwise."""
        github = FakeGitHub("")
        with self.assertRaises(ValueError):
            audit.append_record("o/qzp", "a.jsonl", "not json", branch="main", message="m", runner=github)
        self.assertEqual(github.puts, [])


class CliTests(unittest.TestCase):
    """The workflow step appends the evaluate step's last line."""

    def _main(self, github: FakeGitHub, record_text: str):
        with tempfile.TemporaryDirectory() as tmp:
            record_file = Path(tmp) / "skip.jsonl"
            record_file.write_text(record_text, encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            argv = ["--repo", "o/qzp", "--path", "audit/skip.jsonl", "--record-file", str(record_file)]
            with redirect_stdout(out), redirect_stderr(err):
                code = audit.main(argv + ["--message", "m"], runner=github)
        return code, out.getvalue(), err.getvalue()

    def test_the_cli_appends_the_last_line(self) -> None:
        """Older lines in the local copy are not re-sent."""
        github = FakeGitHub(None)
        code, out, _ = self._main(github, '{"stale": 1}\n' + RECORD + "\n")
        self.assertEqual(code, 0)
        self.assertEqual(github.content, RECORD + "\n")
        self.assertIn("audit record appended to o/qzp:main/audit/skip.jsonl (attempt 1)", out)

    def test_the_cli_reports_a_failure_as_an_error(self) -> None:
        """A failed append fails the step with an ::error:: annotation."""
        code, _, err = self._main(FakeGitHub("", write_error="denied"), RECORD + "\n")
        self.assertEqual(code, 1)
        self.assertIn("::error::audit record not written: writing audit/skip.jsonl failed: denied", err)
        code, _, err = self._main(FakeGitHub(None), "not json\n")
        self.assertEqual(code, 1)
        self.assertIn("::error::audit record not written:", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
