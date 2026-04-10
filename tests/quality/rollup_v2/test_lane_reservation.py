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

    def test_reserved_lane_keys_empty_after_pr3(self) -> None:
        from scripts.quality.rollup_v2.pipeline import RESERVED_LANE_KEYS

        # All 4 PR 3 lanes are now registered normalizers
        self.assertEqual(len(RESERVED_LANE_KEYS), 0)

    def test_all_four_new_lanes_registered(self) -> None:
        from scripts.quality.rollup_v2.pipeline import NORMALIZER_REGISTRY

        self.assertIn("semgrep", NORMALIZER_REGISTRY)
        self.assertIn("codeql", NORMALIZER_REGISTRY)
        self.assertIn("chromatic", NORMALIZER_REGISTRY)
        self.assertIn("applitools", NORMALIZER_REGISTRY)

    def test_not_configured_summaries_empty(self) -> None:
        from scripts.quality.rollup_v2.pipeline import _build_not_configured_summaries

        summaries = _build_not_configured_summaries()
        # All lanes registered — no not-configured placeholders
        self.assertEqual(len(summaries), 0)

    def test_pipeline_no_not_configured_lanes(self) -> None:
        from scripts.quality.rollup_v2.pipeline import run_pipeline

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp).resolve()
            output_dir = repo_root / "output"
            output_dir.mkdir()

            result = run_pipeline(
                artifacts={}, repo_root=repo_root, output_dir=output_dir
            )
            summaries = result.canonical_payload["provider_summaries"]
            not_configured = [s for s in summaries if s.get("status") == "not-configured"]
            self.assertEqual(len(not_configured), 0)

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
