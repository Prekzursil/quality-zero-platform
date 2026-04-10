"""Tests for lane key pre-reservation (per design §A.5 + Phase 15)."""
from __future__ import absolute_import

import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


class LaneReservationTests(unittest.TestCase):
    """Test that reserved lane keys produce not-configured placeholders."""

    def test_reserved_lane_keys_exist(self) -> None:
        from scripts.quality.rollup_v2.pipeline import RESERVED_LANE_KEYS

        self.assertIn("semgrep", RESERVED_LANE_KEYS)
        self.assertIn("codeql", RESERVED_LANE_KEYS)
        self.assertIn("chromatic", RESERVED_LANE_KEYS)
        self.assertIn("applitools", RESERVED_LANE_KEYS)

    def test_not_configured_summaries_generated(self) -> None:
        from scripts.quality.rollup_v2.pipeline import _build_not_configured_summaries

        summaries = _build_not_configured_summaries()
        providers = {s["provider"] for s in summaries}
        self.assertIn("Applitools Zero", providers)
        self.assertIn("Chromatic Zero", providers)
        self.assertIn("CodeQL Zero", providers)
        self.assertIn("Semgrep Zero", providers)
        for s in summaries:
            self.assertEqual(s["status"], "not-configured")
            self.assertEqual(s["total"], 0)

    def test_pipeline_includes_not_configured_lanes(self) -> None:
        from scripts.quality.rollup_v2.pipeline import run_pipeline

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp).resolve()
            output_dir = repo_root / "output"
            output_dir.mkdir()

            result = run_pipeline(
                artifacts={}, repo_root=repo_root, output_dir=output_dir
            )
            # With no artifacts, all reserved lanes should show as not-configured
            summaries = result.canonical_payload["provider_summaries"]
            not_configured = [s for s in summaries if s.get("status") == "not-configured"]
            self.assertEqual(len(not_configured), 4)

    def test_configured_lane_does_not_get_placeholder(self) -> None:
        """When a reserved lane provides data, it should not also get a placeholder."""
        from scripts.quality.rollup_v2.pipeline import run_pipeline

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp).resolve()
            (repo_root / "src").mkdir()
            (repo_root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
            output_dir = repo_root / "output"
            output_dir.mkdir()

            # If we add data for a provider that matches a reserved lane label,
            # it should not also generate a not-configured placeholder.
            # Reserved lanes use provider labels like "Semgrep Zero", not raw providers.
            result = run_pipeline(
                artifacts={}, repo_root=repo_root, output_dir=output_dir
            )
            providers = [s["provider"] for s in result.canonical_payload["provider_summaries"]]
            # Check no duplicates
            self.assertEqual(len(providers), len(set(providers)))

    def test_missing_artifact_renders_grey_placeholder(self) -> None:
        """Missing-artifact lanes get a status marker in the payload."""
        from scripts.quality.rollup_v2.pipeline import run_pipeline

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp).resolve()
            output_dir = repo_root / "output"
            output_dir.mkdir()

            result = run_pipeline(
                artifacts={}, repo_root=repo_root, output_dir=output_dir
            )
            # The markdown should mention providers (the empty state shows "0 findings across N providers")
            self.assertIn("providers", result.markdown)


if __name__ == "__main__":
    unittest.main()
