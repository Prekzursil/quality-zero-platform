"""Tests for the quality-secrets preflight report (``scripts/quality/check_quality_secrets.py``).

``tests/test_check_quality_secrets.py`` covers the alert opener; these cover the
report itself: argument parsing, the pass/fail rule, the Markdown, the CLI, and
when ``--open-alerts`` fires. Every CLI run happens inside ``isolated_cwd`` so
the default report paths never land in the repository.
"""

from __future__ import absolute_import

import importlib.util
import io
import json
import sys
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import List
from unittest.mock import patch

from tests.workspace_isolation import isolated_cwd

from scripts.quality import check_quality_secrets as cqs


class BuildPayloadTests(unittest.TestCase):
    """Only REQUIRED secrets and variables decide the status; conditional ones are reported."""

    def test_status_and_missing_lists(self) -> None:
        """A blank value counts as missing, exactly like an unset one."""
        env = {"QZP_T_SECRET_OK": "s3cret", "QZP_T_SECRET_BLANK": "  ", "QZP_T_VAR_OK": "v"}
        with patch.dict("os.environ", env, clear=False):
            payload = cqs._build_payload(
                required_secrets=["QZP_T_SECRET_OK", "QZP_T_SECRET_BLANK"],
                conditional_secrets=["QZP_T_COND_SECRET"],
                required_vars=["QZP_T_VAR_OK"],
                conditional_vars=["QZP_T_COND_VAR"],
            )
        self.assertEqual(payload["status"], "fail")
        self.assertEqual(payload["missing_secrets"], ["QZP_T_SECRET_BLANK"])
        self.assertEqual(payload["missing_conditional_secrets"], ["QZP_T_COND_SECRET"])
        self.assertEqual(payload["missing_vars"], [])
        self.assertEqual(payload["missing_conditional_vars"], ["QZP_T_COND_VAR"])

    def test_missing_conditionals_alone_still_pass(self) -> None:
        """Conditional secrets and variables never fail the preflight."""
        with patch.dict("os.environ", {"QZP_T_SECRET_OK": "s", "QZP_T_VAR_OK": "v"}, clear=False):
            payload = cqs._build_payload(
                required_secrets=["QZP_T_SECRET_OK"],
                conditional_secrets=["QZP_T_UNSET_A"],
                required_vars=["QZP_T_VAR_OK"],
                conditional_vars=["QZP_T_UNSET_B"],
            )
        self.assertEqual(payload["status"], "pass")

    def test_a_missing_required_variable_fails(self) -> None:
        """Variables are held to the same rule as secrets."""
        payload = cqs._build_payload(
            required_secrets=[], conditional_secrets=[], required_vars=["QZP_T_UNSET_VAR"], conditional_vars=[]
        )
        self.assertEqual((payload["status"], payload["missing_vars"]), ("fail", ["QZP_T_UNSET_VAR"]))


class RenderMarkdownTests(unittest.TestCase):
    """Every section is rendered; an empty one says None."""

    def test_sections_list_items_or_none(self) -> None:
        """Missing items are code-quoted bullets; empty sections read `- None`."""
        markdown = cqs._render_md(
            {
                "status": "fail",
                "timestamp_utc": "2026-10-24T00:00:00Z",
                "missing_secrets": ["SONAR_TOKEN"],
                "missing_vars": [],
            }
        )
        self.assertTrue(markdown.startswith("# Quality Secrets Preflight\n\n- Status: `fail`\n"))
        self.assertIn("## Missing secrets\n- `SONAR_TOKEN`\n", markdown)
        self.assertIn("## Missing conditional secrets\n- None\n", markdown)
        self.assertIn("## Missing variables\n- None\n", markdown)
        self.assertTrue(markdown.endswith("## Missing conditional variables\n- None\n"))


class MaybeOpenAlertsTests(unittest.TestCase):
    """Alerts open only when asked AND something required is missing."""

    def _args(self, **overrides) -> Namespace:
        values = {
            "open_alerts": True,
            "dry_run_alerts": True,
            "platform_slug": "Prekzursil/quality-zero-platform",
            "target_repo_slug": "",
        }
        values.update(overrides)
        return Namespace(**values)

    def test_no_flag_or_nothing_missing_opens_nothing(self) -> None:
        """Either condition false: the opener is never called."""
        with patch.object(cqs, "open_secret_missing_alerts") as opener:
            cqs._maybe_open_missing_alerts(self._args(open_alerts=False), {"missing_secrets": ["A"]})
            cqs._maybe_open_missing_alerts(self._args(), {"missing_secrets": []})
        opener.assert_not_called()

    def test_target_defaults_to_the_platform_repo(self) -> None:
        """An empty --target-repo-slug alerts about the platform repo itself."""
        with patch.object(cqs, "open_secret_missing_alerts") as opener:
            cqs._maybe_open_missing_alerts(self._args(), {"missing_secrets": ["A"]})
            cqs._maybe_open_missing_alerts(
                self._args(target_repo_slug="o/r", dry_run_alerts=False), {"missing_secrets": ["B"]}
            )
        self.assertEqual(
            [call.kwargs for call in opener.call_args_list],
            [
                {
                    "platform_slug": "Prekzursil/quality-zero-platform",
                    "target_repo_slug": "Prekzursil/quality-zero-platform",
                    "missing_secrets": ["A"],
                    "dry_run": True,
                },
                {
                    "platform_slug": "Prekzursil/quality-zero-platform",
                    "target_repo_slug": "o/r",
                    "missing_secrets": ["B"],
                    "dry_run": False,
                },
            ],
        )


class MainTests(unittest.TestCase):
    """The CLI end to end, from argv to the written reports and the exit code."""

    def _main(self, argv: List[str]):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["check_quality_secrets.py", *argv]), redirect_stdout(out), redirect_stderr(err):
            code = cqs.main()
        return code, out.getvalue(), err.getvalue()

    def test_pass_writes_both_reports_and_deduplicates(self) -> None:
        """Repeated flags collapse; the reports land where asked; the run passes."""
        with isolated_cwd() as cwd, patch.dict("os.environ", {"QZP_T_S": "x", "QZP_T_V": "y"}, clear=False):
            code, out, _ = self._main(
                [
                    "--required-secret",
                    "QZP_T_S",
                    "--required-secret",
                    "QZP_T_S",
                    "--required-var",
                    "QZP_T_V",
                    "--out-json",
                    "reports/s.json",
                    "--out-md",
                    "reports/s.md",
                ]
            )
            payload = json.loads((cwd / "reports" / "s.json").read_text(encoding="utf-8"))
            self.assertTrue((cwd / "reports" / "s.md").is_file())
        self.assertEqual(code, 0)
        self.assertEqual(payload["required_secrets"], ["QZP_T_S"])
        self.assertIn("- Status: `pass`", out)

    def test_a_missing_required_secret_fails_and_dry_run_alerts_never_call_gh(self) -> None:
        """--open-alerts --dry-run-alerts composes the alert without any gh call; the run fails."""
        with isolated_cwd() as cwd, patch.object(cqs.alerts, "open_alert_issue") as real_opener:
            code, _, _ = self._main(["--required-secret", "QZP_T_UNSET_SECRET", "--open-alerts", "--dry-run-alerts"])
            self.assertTrue((cwd / "quality-secrets" / "secrets.json").is_file())
        self.assertEqual(code, 1)
        real_opener.assert_not_called()

    def test_an_escaping_report_path_fails_before_any_alert(self) -> None:
        """A report path outside the workspace is refused, and nothing else happens."""
        with isolated_cwd(), patch.object(cqs, "open_secret_missing_alerts") as opener:
            code, _, err = self._main(
                ["--required-secret", "QZP_T_UNSET_SECRET", "--open-alerts", "--out-json", "../../escape.json"]
            )
        self.assertEqual(code, 1)
        self.assertIn("escapes", err)
        opener.assert_not_called()


class BootstrapTests(unittest.TestCase):
    """Loaded by path from a foreign cwd, the script puts the repo root on sys.path."""

    def test_both_bootstrap_branches(self) -> None:
        """Root absent: inserted once. Root present: left alone."""
        module_path = Path(cqs.__file__).resolve()
        repo_root = str(module_path.parents[2])
        for root_on_path in (False, True):
            with self.subTest(root_on_path=root_on_path):
                spec = importlib.util.spec_from_file_location("check_quality_secrets_under_test", module_path)
                if spec is None or spec.loader is None:  # pragma: no cover - environment guard
                    self.fail("no module spec for check_quality_secrets")
                module = importlib.util.module_from_spec(spec)
                original = list(sys.path)
                try:
                    sys.path[:] = [entry for entry in sys.path if entry != repo_root]
                    if root_on_path:
                        sys.path.insert(0, repo_root)
                    spec.loader.exec_module(module)
                    self.assertEqual(sys.path.count(repo_root), 1)
                finally:
                    sys.path[:] = original
                self.assertEqual(module.NONE_BULLET, "- None")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
