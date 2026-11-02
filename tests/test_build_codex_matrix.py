"""Tests for ``scripts/build_codex_matrix.py``, the Codex fleet-wave dispatch planner."""

from __future__ import absolute_import

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List

from scripts import build_codex_matrix as matrix

CONFIG: Dict[str, Any] = {
    "defaults": {"model": "m-default", "effort": "high", "max_changed_files": 30},
    "targets": [
        {"repo": "Prekzursil/Reframe", "enabled": True, "tasks": ["Docs only: tidy", "  "]},
        {
            "repo": "Prekzursil/WebCoder",
            "enabled": True,
            "tasks": ["Tests only: cover x"],
            "max_changed_files": 5,
            "model": "m-override",
            "effort": "low",
        },
        {"repo": "Prekzursil/Off", "enabled": False, "tasks": ["never in a sweep"]},
        {"repo": "Prekzursil/NoTasks", "enabled": True},
        {"repo": "Prekzursil/BadTasks", "enabled": True, "tasks": "not-a-list"},
        {"repo": "", "enabled": True, "tasks": ["no repo"]},
        "not-a-target",
    ],
}


class HelperTests(unittest.TestCase):
    """Name matching and the per-repo cap."""

    def test_normalize_repo_takes_the_bare_name_case_folded(self) -> None:
        """`owner/Name` and `name` match the same target."""
        self.assertEqual(matrix.normalize_repo("Prekzursil/WebCoder "), "webcoder")
        self.assertEqual(matrix.normalize_repo("WebCoder"), "webcoder")

    def test_coerce_cap_accepts_only_positive_integers(self) -> None:
        """Anything else falls back."""
        self.assertEqual(matrix._coerce_cap("12", 40), 12)
        for bad in (None, "many", 0, -3):
            with self.subTest(bad=bad):
                self.assertEqual(matrix._coerce_cap(bad, 40), 40)


class LoadConfigTests(unittest.TestCase):
    """Structural errors exit 2 with a message; a mapping loads."""

    def setUp(self) -> None:
        """A scratch config directory."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _load(self, text: str):
        path = self.tmp / "codex-targets.yml"
        path.write_text(text, encoding="utf-8")
        err = io.StringIO()
        with redirect_stderr(err):
            return matrix.load_config(path), err.getvalue()

    def test_a_mapping_loads(self) -> None:
        """The happy path returns the parsed document."""
        config, _ = self._load("targets: []\n")
        self.assertEqual(config, {"targets": []})

    def test_structural_errors_exit_2(self) -> None:
        """Unreadable, unparseable, or not a mapping."""
        cases = [("- a list\n", "must be a YAML mapping"), ("a: [unclosed\n", "invalid YAML")]
        for text, message in cases:
            with self.subTest(message=message):
                err = io.StringIO()
                path = self.tmp / "bad.yml"
                path.write_text(text, encoding="utf-8")
                with redirect_stderr(err), self.assertRaises(SystemExit) as raised:
                    matrix.load_config(path)
                self.assertEqual(raised.exception.code, 2)
                self.assertIn(message, err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            matrix.load_config(self.tmp / "absent.yml")
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("cannot read config", err.getvalue())


class BuildEntriesTests(unittest.TestCase):
    """A sweep takes enabled targets with tasks; a single-repo run bypasses `enabled`."""

    def _build(self, config: Dict[str, Any], only_repo: Any = None):
        err = io.StringIO()
        with redirect_stderr(err):
            entries = matrix.build_entries(config, only_repo=only_repo)
        return entries, err.getvalue()

    def test_a_full_sweep(self) -> None:
        """Defaults merge under per-repo overrides; disabled, task-less and malformed targets are skipped."""
        entries, err = self._build(CONFIG)
        self.assertEqual(err, "")
        self.assertEqual(
            entries,
            [
                {
                    "repo": "Prekzursil/Reframe",
                    "task": "Docs only: tidy",
                    "model": "m-default",
                    "effort": "high",
                    "max_changed_files": 30,
                },
                {
                    "repo": "Prekzursil/WebCoder",
                    "task": "Tests only: cover x",
                    "model": "m-override",
                    "effort": "low",
                    "max_changed_files": 5,
                },
            ],
        )

    def test_missing_defaults_fall_back(self) -> None:
        """No defaults block uses the built-in fallbacks."""
        entries, _ = self._build({"targets": [{"repo": "o/r", "enabled": True, "tasks": ["t"]}]})
        self.assertEqual(
            entries, [{"repo": "o/r", "task": "t", "model": "gpt-5.6-sol", "effort": "max", "max_changed_files": 40}]
        )

    def test_only_repo_bypasses_enabled(self) -> None:
        """Naming a repo in a manual run is an intentional override."""
        entries, err = self._build(CONFIG, only_repo="off")
        self.assertEqual([entry["task"] for entry in entries], ["never in a sweep"])
        self.assertEqual(err, "")

    def test_only_repo_warnings(self) -> None:
        """An unknown repo, or a known one without tasks, warns and yields nothing."""
        entries, err = self._build(CONFIG, only_repo="Prekzursil/Ghost")
        self.assertEqual(entries, [])
        self.assertIn("matched no target", err)
        entries, err = self._build(CONFIG, only_repo="notasks")
        self.assertEqual(entries, [])
        self.assertIn("has no tasks defined", err)

    def test_structural_errors_exit_2(self) -> None:
        """`defaults` must be a mapping and `targets` a list."""
        for config, message in (
            ({"defaults": ["x"]}, "'defaults' must be a mapping"),
            ({"targets": "x"}, "'targets' must be a list"),
        ):
            with self.subTest(message=message):
                err = io.StringIO()
                with redirect_stderr(err), self.assertRaises(SystemExit) as raised:
                    matrix.build_entries(config)
                self.assertEqual(raised.exception.code, 2)
                self.assertIn(message, err.getvalue())


class MainTests(unittest.TestCase):
    """The CLI prints the plan as an array or a matrix object."""

    def setUp(self) -> None:
        """Write the shared config to a scratch file."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = Path(self._tmp.name) / "codex-targets.yml"
        self.config.write_text(json.dumps(CONFIG), encoding="utf-8")

    def _main(self, argv: List[str]) -> str:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(matrix.main(["--config", str(self.config), *argv]), 0)
        return out.getvalue()

    def test_array_output_is_compact_json_on_one_line(self) -> None:
        """The default shape is a JSON list, one line, newline-terminated."""
        printed = self._main([])
        self.assertTrue(printed.endswith("\n"))
        self.assertEqual(printed.count("\n"), 1)
        self.assertEqual(
            [entry["repo"] for entry in json.loads(printed)], ["Prekzursil/Reframe", "Prekzursil/WebCoder"]
        )

    def test_matrix_output_wraps_entries_in_include(self) -> None:
        """`--format matrix --pretty` is a GitHub Actions matrix, indented."""
        printed = self._main(["--format", "matrix", "--pretty", "--only-repo", " Prekzursil/WebCoder "])
        self.assertEqual(json.loads(printed), {"include": [CONFIG_WEBCODER_ENTRY]})
        self.assertIn('\n  "include"', printed)

    def test_a_blank_only_repo_is_a_full_sweep(self) -> None:
        """Whitespace-only --only-repo does not select anything special."""
        self.assertEqual(len(json.loads(self._main(["--only-repo", "   "]))), 2)


CONFIG_WEBCODER_ENTRY = {
    "repo": "Prekzursil/WebCoder",
    "task": "Tests only: cover x",
    "model": "m-override",
    "effort": "low",
    "max_changed_files": 5,
}


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
