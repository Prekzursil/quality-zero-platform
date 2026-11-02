"""Malformed GitHub payload shapes in the Lane-8 gate panel (``build_admin_dashboard``).

The API is trusted for its envelope, not its body: a check-runs page that is
not an object, or a HEAD commit without a ``commit`` block, must degrade to
"nothing from that part" and keep walking, never crash the dashboard build.
"""

from __future__ import absolute_import

import unittest
from typing import Any, Dict, Tuple
from unittest.mock import patch

from scripts.quality import build_admin_dashboard

HEAD_SHA = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b"
PROFILE = {"required_contexts": {"target": ["quality / quality"]}}


def _fake(responses: Dict[str, Tuple[Any, Any]]):
    """A ``_github_fetch_degraded`` stand-in answering by URL substring."""

    def fake(url: str, token: str):
        for needle, result in responses.items():
            if needle in url:
                return result
        raise AssertionError(f"unexpected url {url}")

    return fake


class PayloadShapeTests(unittest.TestCase):
    """Odd bodies are skipped, not trusted and not fatal."""

    def test_a_non_object_check_runs_page_adds_nothing_and_the_walk_continues(self) -> None:
        """A list where an object belongs contributes no runs; the next page still counts."""
        fake = _fake(
            {
                "page=1": (["not", "an", "object"], {"link": '<https://api.github.com/x?page=2>; rel="next"'}),
                "page=2": ({"check_runs": [{"name": "quality / quality"}, "junk"]}, {}),
            }
        )
        with patch.object(build_admin_dashboard, "_github_fetch_degraded", side_effect=fake):
            runs, partial = build_admin_dashboard._collect_all_check_runs("https://api.github.com/x?page=1", "t")
        self.assertEqual(runs, [{"name": "quality / quality"}])
        self.assertFalse(partial)

    def test_a_head_without_commit_metadata_still_measures_its_gates(self) -> None:
        """No `commit` block means no head date, not a stale row."""
        fake = _fake(
            {
                "/commits/main": ({"sha": HEAD_SHA, "commit": "not-an-object"}, {}),
                "/check-runs": (
                    {"check_runs": [{"name": "quality / quality", "status": "completed", "conclusion": "success"}]},
                    {},
                ),
                "/status": ({"statuses": []}, {}),
            }
        )
        with patch.object(build_admin_dashboard, "_github_fetch_degraded", side_effect=fake):
            gates = build_admin_dashboard._lane_gates("t", "Prekzursil/x", "main", PROFILE)
        self.assertEqual(gates["gate_status"], "pass")
        self.assertEqual((gates["gate_head_sha"], gates["gate_head_date"]), (HEAD_SHA, ""))
        self.assertEqual(gates["gates"], [{"context": "quality / quality", "status": "pass"}])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
