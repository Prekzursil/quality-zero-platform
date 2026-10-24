"""Tests for the visual-regression zero gates (Applitools, Chromatic)."""

from __future__ import absolute_import

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Callable, Dict, List

from scripts.quality import check_applitools_zero as applitools
from scripts.quality import check_chromatic_zero as chromatic


class _GateHarness(unittest.TestCase):
    """A scratch directory plus a CLI runner capturing stdout / stderr."""

    def setUp(self) -> None:
        """Create the scratch directory."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def write_json(self, document: Any) -> str:
        """Write ``document`` as the gate's input file."""
        path = self.tmp / "results.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return str(path)

    def run_main(self, main: Callable[[List[str]], int], argv: List[str]):
        """Run a gate's ``main`` and capture its streams."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()


class ApplitoolsZeroTests(_GateHarness):
    """Pass only when no step is unresolved and none failed."""

    def test_check_reads_steps_info(self) -> None:
        """Counts come from ``stepsInfo``; a non-mapping block cannot pass."""
        self.assertEqual(applitools._check_applitools({"stepsInfo": {"total": 4}}), (True, 4, 0, 0))
        self.assertEqual(
            applitools._check_applitools({"stepsInfo": {"total": 4, "unresolved": 1, "failed": 2}}), (False, 4, 1, 2)
        )
        self.assertEqual(applitools._check_applitools({"stepsInfo": ["junk"]}), (False, 0, 0, 0))
        self.assertEqual(applitools._check_applitools({}), (True, 0, 0, 0))

    def test_missing_input_fails(self) -> None:
        """No results file is a failure."""
        code, _, err = self.run_main(applitools.main, ["--json", str(self.tmp / "absent.json")])
        self.assertEqual(code, 1)
        self.assertIn("Applitools JSON not found", err)

    def test_clean_run_passes_with_both_summaries(self) -> None:
        """A clean run passes and writes the JSON and Markdown summaries."""
        out_json = self.tmp / "out" / "a.json"
        out_md = self.tmp / "out" / "a.md"
        code, out, _ = self.run_main(
            applitools.main,
            [
                "--json",
                self.write_json({"stepsInfo": {"total": 3}}),
                "--out-json",
                str(out_json),
                "--out-md",
                str(out_md),
            ],
        )
        self.assertEqual(code, 0)
        self.assertIn("Applitools zero gate passed", out)
        self.assertEqual(
            json.loads(out_json.read_text(encoding="utf-8")),
            {"provider": "Applitools", "total_steps": 3, "unresolved": 0, "failed": 0, "pass": True},
        )
        self.assertIn("**Status:** PASS", out_md.read_text(encoding="utf-8"))

    def test_regressions_fail_with_a_fail_summary(self) -> None:
        """Unresolved or failed steps fail the gate."""
        out_md = self.tmp / "a.md"
        code, _, err = self.run_main(
            applitools.main,
            ["--json", self.write_json({"stepsInfo": {"total": 3, "unresolved": 1}}), "--out-md", str(out_md)],
        )
        self.assertEqual(code, 1)
        self.assertIn("Applitools zero gate FAILED: 1 unresolved, 0 failed.", err)
        self.assertIn("**Status:** FAIL", out_md.read_text(encoding="utf-8"))

    def test_no_summary_paths_write_nothing(self) -> None:
        """Without --out-json / --out-md only the verdict is printed."""
        code, _, _ = self.run_main(applitools.main, ["--json", self.write_json({"stepsInfo": {"failed": 1}})])
        self.assertEqual(code, 1)
        self.assertEqual(sorted(path.name for path in self.tmp.iterdir()), ["results.json"])


class ChromaticZeroTests(_GateHarness):
    """Pass only when nothing errored, changed or was rejected."""

    def test_check_reads_the_summary(self) -> None:
        """Errored, rejected and changed snapshots all block; a non-mapping summary cannot pass."""
        clean: Dict[str, int] = {"total": 5, "accepted": 5}
        self.assertEqual(chromatic._check_chromatic({"summary": clean}), (True, 5, 5, 0))
        for key in ("errored", "rejected", "changed"):
            with self.subTest(key=key):
                passed, *_ = chromatic._check_chromatic({"summary": dict(clean, **{key: 1})})
                self.assertFalse(passed)
        self.assertEqual(chromatic._check_chromatic({"summary": "junk"}), (False, 0, 0, 0))

    def test_missing_input_fails(self) -> None:
        """No results file is a failure."""
        code, _, err = self.run_main(chromatic.main, ["--json", str(self.tmp / "absent.json")])
        self.assertEqual(code, 1)
        self.assertIn("Chromatic JSON not found", err)

    def test_clean_run_passes_with_both_summaries(self) -> None:
        """A clean run passes and writes the JSON and Markdown summaries."""
        out_json = self.tmp / "out" / "c.json"
        out_md = self.tmp / "out" / "c.md"
        code, out, _ = self.run_main(
            chromatic.main,
            [
                "--json",
                self.write_json({"summary": {"total": 2, "accepted": 2}}),
                "--out-json",
                str(out_json),
                "--out-md",
                str(out_md),
            ],
        )
        self.assertEqual(code, 0)
        self.assertIn("Chromatic zero gate passed", out)
        self.assertEqual(
            json.loads(out_json.read_text(encoding="utf-8")),
            {"provider": "Chromatic", "total_snapshots": 2, "accepted": 2, "errored": 0, "pass": True},
        )
        self.assertIn("**Status:** PASS", out_md.read_text(encoding="utf-8"))

    def test_regressions_fail(self) -> None:
        """A changed snapshot fails the gate; no summaries are required."""
        code, _, err = self.run_main(
            chromatic.main, ["--json", self.write_json({"summary": {"total": 2, "accepted": 1, "changed": 1}})]
        )
        self.assertEqual(code, 1)
        self.assertIn("Chromatic zero gate FAILED", err)
        self.assertEqual(sorted(path.name for path in self.tmp.iterdir()), ["results.json"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
