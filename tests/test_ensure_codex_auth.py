"""Tests for ``scripts/quality/ensure_codex_auth.py`` (Codex account auth on a trusted runner).

Every test passes an explicit ``--auth-file`` inside a scratch directory: the
default (``~/.codex/auth.json``) must never be touched by the test suite.
"""

from __future__ import absolute_import

import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.quality import ensure_codex_auth as auth

ENV = "QZP_TEST_CODEX_AUTH_JSON"


class EnsureCodexAuthTests(unittest.TestCase):
    """An existing auth file wins; otherwise the bootstrap secret seeds it; otherwise fail."""

    def setUp(self) -> None:
        """A scratch home for the auth file and the JSON summary."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.auth_file = self.tmp / "codex" / "auth.json"

    def _args(self) -> Namespace:
        return Namespace(auth_file=str(self.auth_file), bootstrap_env_var=ENV, out_json="")

    def test_an_existing_non_empty_auth_file_is_kept(self) -> None:
        """A seeded runner keeps its auth; the bootstrap secret is not consulted."""
        self.auth_file.parent.mkdir(parents=True)
        self.auth_file.write_text('{"token": "seeded"}', encoding="utf-8")
        with patch.dict("os.environ", {ENV: '{"token": "other"}'}):
            payload = auth._resolve_auth_payload(self._args())
        self.assertEqual(payload, {"auth_file": str(self.auth_file), "source": "existing"})
        self.assertEqual(self.auth_file.read_text(encoding="utf-8"), '{"token": "seeded"}')

    def test_an_empty_auth_file_is_reseeded_from_the_bootstrap_secret(self) -> None:
        """A zero-byte file is not auth; the secret's JSON is written, normalised."""
        self.auth_file.parent.mkdir(parents=True)
        self.auth_file.write_text("", encoding="utf-8")
        with patch.dict("os.environ", {ENV: ' {"b": 1, "a": 2} '}):
            payload = auth._resolve_auth_payload(self._args())
        self.assertEqual(payload, {"auth_file": str(self.auth_file), "source": ENV})
        self.assertEqual(self.auth_file.read_text(encoding="utf-8"), '{\n  "a": 2,\n  "b": 1\n}\n')

    def test_a_missing_auth_file_is_created_from_the_bootstrap_secret(self) -> None:
        """The parent directory is created too."""
        with patch.dict("os.environ", {ENV: '{"token": "x"}'}):
            payload = auth._resolve_auth_payload(self._args())
        self.assertEqual(payload["source"], ENV)
        self.assertEqual(json.loads(self.auth_file.read_text(encoding="utf-8")), {"token": "x"})

    def test_an_invalid_bootstrap_secret_fails_loudly(self) -> None:
        """Malformed JSON is an error naming the variable, and nothing is written."""
        with (
            patch.dict("os.environ", {ENV: "{not json"}),
            self.assertRaisesRegex(SystemExit, f"{ENV} is not valid JSON"),
        ):
            auth._resolve_auth_payload(self._args())
        self.assertFalse(self.auth_file.exists())

    def test_no_auth_and_no_secret_fails_with_the_fix(self) -> None:
        """Neither source: the error says how to seed the runner."""
        with patch.dict("os.environ", {ENV: "  "}), self.assertRaisesRegex(SystemExit, "codex login"):
            auth._resolve_auth_payload(self._args())

    def test_write_payload_skips_an_empty_path_and_writes_a_given_one(self) -> None:
        """The JSON summary is optional."""
        auth._write_payload("", {"x": 1})
        self.assertEqual(list(self.tmp.iterdir()), [])
        target = self.tmp / "out" / "auth.json"
        auth._write_payload(str(target), {"source": "existing", "auth_file": "f"})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"auth_file": "f", "source": "existing"})

    def test_main_parses_args_writes_the_summary_and_prints_it(self) -> None:
        """The CLI end to end, from argv."""
        out_json = self.tmp / "summary.json"
        argv = [
            "ensure_codex_auth.py",
            "--auth-file",
            str(self.auth_file),
            "--bootstrap-env-var",
            ENV,
            "--out-json",
            str(out_json),
        ]
        out = io.StringIO()
        with patch.object(sys, "argv", argv), patch.dict("os.environ", {ENV: '{"t": 1}'}), redirect_stdout(out):
            self.assertEqual(auth.main(), 0)
        expected = {"auth_file": str(self.auth_file), "source": ENV}
        self.assertEqual(json.loads(out.getvalue()), expected)
        self.assertEqual(json.loads(out_json.read_text(encoding="utf-8")), expected)

    def test_parse_args_defaults(self) -> None:
        """The documented defaults: the user's codex auth file and the CODEX_AUTH_JSON secret."""
        with patch.object(sys, "argv", ["ensure_codex_auth.py"]):
            args = auth._parse_args()
        self.assertEqual(
            (args.auth_file, args.bootstrap_env_var, args.out_json), ("~/.codex/auth.json", "CODEX_AUTH_JSON", "")
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
