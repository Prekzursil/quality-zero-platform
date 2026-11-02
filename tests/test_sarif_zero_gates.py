"""Tests for the SARIF zero-finding gates (``_sarif_zero_gate`` and its CodeQL / Semgrep wrappers)."""

from __future__ import absolute_import

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List

from scripts.quality import _sarif_zero_gate as gate

_QUALITY_DIR = Path(gate.__file__).resolve().parent
_REPO_ROOT = str(_QUALITY_DIR.parents[1])


def _sarif(*result_counts: int) -> Dict[str, Any]:
    """A SARIF document with one run per count, each holding that many results."""
    return {"runs": [{"results": [{"ruleId": f"r{i}"} for i in range(count)]} for count in result_counts]}


class CountSarifResultsTests(unittest.TestCase):
    """Results are summed across every well-formed run."""

    def test_counts_every_run(self) -> None:
        """Two runs with 2 and 3 results are 5 findings."""
        self.assertEqual(gate.count_sarif_results(_sarif(2, 3)), 5)

    def test_malformed_runs_and_results_count_as_nothing(self) -> None:
        """A non-mapping run or a non-list results value adds zero, never crashes."""
        data = {"runs": ["not-a-run", {"results": "nope"}, {"results": [{}]}, {}]}
        self.assertEqual(gate.count_sarif_results(data), 1)
        self.assertEqual(gate.count_sarif_results({}), 0)


class RunZeroGateTests(unittest.TestCase):
    """The CLI: exit codes, summaries, and the missing-file path."""

    def setUp(self) -> None:
        """A scratch directory for SARIF inputs and summaries."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _run(self, argv: List[str]):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = gate.run_zero_gate(provider="CodeQL", argv=argv)
        return code, out.getvalue(), err.getvalue()

    def _write_sarif(self, document: Dict[str, Any]) -> str:
        path = self.tmp / "in.sarif"
        path.write_text(json.dumps(document), encoding="utf-8")
        return str(path)

    def test_missing_sarif_fails(self) -> None:
        """No SARIF file is a failure, never a pass."""
        code, _, err = self._run(["--sarif", str(self.tmp / "absent.sarif")])
        self.assertEqual(code, 1)
        self.assertIn("SARIF file not found", err)

    def test_zero_findings_pass_and_write_both_summaries(self) -> None:
        """A clean SARIF passes and both summaries say PASS."""
        out_json = self.tmp / "nested" / "summary.json"
        out_md = self.tmp / "nested" / "summary.md"
        code, out, _ = self._run(
            ["--sarif", self._write_sarif(_sarif(0)), "--out-json", str(out_json), "--out-md", str(out_md)]
        )
        self.assertEqual(code, 0)
        self.assertIn("CodeQL zero gate passed: 0 findings.", out)
        self.assertEqual(
            json.loads(out_json.read_text(encoding="utf-8")),
            {"provider": "CodeQL", "total_findings": 0, "pass": True},
        )
        self.assertEqual(out_md.read_text(encoding="utf-8"), "# CodeQL Zero\n\n**Status:** PASS\n**Findings:** 0\n")

    def test_findings_fail_with_a_fail_summary(self) -> None:
        """Any finding fails; the Markdown summary says FAIL and counts them."""
        out_md = self.tmp / "summary.md"
        code, _, err = self._run(["--sarif", self._write_sarif(_sarif(1, 1)), "--out-md", str(out_md)])
        self.assertEqual(code, 1)
        self.assertIn("CodeQL zero gate FAILED: 2 finding(s) detected.", err)
        self.assertIn("**Status:** FAIL\n**Findings:** 2", out_md.read_text(encoding="utf-8"))

    def test_no_summary_paths_write_nothing(self) -> None:
        """Without --out-json / --out-md only the verdict is printed."""
        code, _, _ = self._run(["--sarif", self._write_sarif(_sarif(0))])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(path.name for path in self.tmp.iterdir()), ["in.sarif"])


class WrapperModuleTests(unittest.TestCase):
    """The per-analyzer wrappers import the shared gate, bootstrapping the repo root when needed."""

    def _load(self, name: str, *, root_on_path: bool) -> Any:
        """Execute the wrapper file as a module (never as ``__main__``)."""
        module_path = _QUALITY_DIR / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"{name}_under_test", module_path)
        if spec is None or spec.loader is None:  # pragma: no cover - environment guard
            self.fail(f"no module spec for {module_path}")
        module = importlib.util.module_from_spec(spec)
        original = list(sys.path)
        try:
            if root_on_path:
                if _REPO_ROOT not in sys.path:
                    sys.path.insert(0, _REPO_ROOT)
            else:
                sys.path[:] = [entry for entry in sys.path if entry != _REPO_ROOT]
            spec.loader.exec_module(module)
            self.assertIn(_REPO_ROOT, sys.path)
        finally:
            sys.path[:] = original
        return module

    def test_both_wrappers_dispatch_to_the_shared_gate(self) -> None:
        """CodeQL and Semgrep share one implementation."""
        for name in ("check_codeql_zero", "check_semgrep_zero"):
            with self.subTest(name=name):
                module = self._load(name, root_on_path=True)
                self.assertIs(module.run_zero_gate, gate.run_zero_gate)

    def test_semgrep_wrapper_bootstraps_the_repo_root(self) -> None:
        """Invoked by path from a foreign cwd, the wrapper puts the repo root on sys.path itself."""
        module = self._load("check_semgrep_zero", root_on_path=False)
        self.assertIs(module.run_zero_gate, gate.run_zero_gate)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
