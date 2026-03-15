from __future__ import annotations

import unittest

from scripts.quality.check_required_checks import _evaluate


class RequiredChecksTests(unittest.TestCase):
    def test_reusable_workflow_check_run_suffix_satisfies_bare_required_context(self) -> None:
        status, missing, failed = _evaluate(
            ["Coverage 100 Gate", "Semgrep Zero"],
            {
                "shared-scanner-matrix / Coverage 100 Gate": {
                    "state": "completed",
                    "conclusion": "success",
                    "source": "check_run",
                },
                "shared-scanner-matrix / Semgrep Zero": {
                    "state": "completed",
                    "conclusion": "success",
                    "source": "check_run",
                },
            },
        )

        self.assertEqual(status, "pass")
        self.assertEqual(missing, [])
        self.assertEqual(failed, [])
