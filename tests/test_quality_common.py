from __future__ import absolute_import

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.quality.common import dedupe_strings, safe_output_path, utc_timestamp, write_report


@contextlib.contextmanager
def _temporary_cwd(target: Path):
    previous = Path.cwd()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(previous)


class QualityCommonTests(unittest.TestCase):
    def test_utc_timestamp_is_timezone_aware(self) -> None:
        timestamp = utc_timestamp()
        parsed = datetime.fromisoformat(timestamp)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_dedupe_strings_trims_skips_empty_and_preserves_first_seen_order(self) -> None:
        self.assertEqual(
            dedupe_strings(["  alpha ", "", "beta", "alpha", None, " beta ", "gamma"]),
            ["alpha", "beta", "gamma"],
        )

    def test_safe_output_path_uses_fallback_and_rejects_workspace_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with _temporary_cwd(root):
                fallback_path = safe_output_path("", "reports/out.json")
                self.assertEqual(fallback_path.name, "out.json")
                self.assertTrue(fallback_path.as_posix().endswith("/reports/out.json"))

                nested_path = safe_output_path("nested/report.md", "ignored.txt")
                self.assertEqual(nested_path.name, "report.md")
                self.assertTrue(nested_path.as_posix().endswith("/nested/report.md"))
                with self.assertRaisesRegex(ValueError, "escapes workspace root"):
                    safe_output_path("../outside.json", "reports/out.json")

    def test_write_report_writes_both_formats_and_prints_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {"status": "pass", "items": ["one", "two"]}
            stdout = io.StringIO()

            with _temporary_cwd(root), contextlib.redirect_stdout(stdout):
                result = write_report(
                    payload,
                    out_json="reports/result.json",
                    out_md="reports/result.md",
                    default_json="fallback.json",
                    default_md="fallback.md",
                    render_md=lambda current: f"# Report\n\n- Status: {current['status']}\n",
                )

            self.assertEqual(result, 0)
            json_payload = json.loads((root / "reports" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(json_payload, payload)
            self.assertEqual(
                (root / "reports" / "result.md").read_text(encoding="utf-8"),
                "# Report\n\n- Status: pass\n",
            )
            self.assertEqual(stdout.getvalue(), "# Report\n\n- Status: pass\n")

    def test_write_report_returns_non_zero_when_output_path_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stderr = io.StringIO()
            with _temporary_cwd(root), contextlib.redirect_stderr(stderr):
                result = write_report(
                    {"status": "fail"},
                    out_json="../escape.json",
                    out_md="reports/result.md",
                    default_json="fallback.json",
                    default_md="fallback.md",
                    render_md=lambda _payload: "ignored\n",
                )

            self.assertEqual(result, 1)
            self.assertIn("escapes workspace root", stderr.getvalue())

