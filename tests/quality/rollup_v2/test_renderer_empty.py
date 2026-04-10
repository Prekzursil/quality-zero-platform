"""Tests for renderer empty-state output (per design §A.1.2)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.renderer import render_markdown
from scripts.quality.rollup_v2.schema.finding import SCHEMA_VERSION


class RendererEmptyStateTests(unittest.TestCase):
    def _empty_payload(self, provider_count: int = 3) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "total_findings": 0,
            "findings": [],
            "provider_summaries": [
                {"provider": f"Provider{i}", "total": 0, "high": 0, "medium": 0, "low": 0}
                for i in range(provider_count)
            ],
            "unmapped_rules": [],
            "normalizer_errors": [],
        }

    def test_zero_findings_celebration_text(self):
        md = render_markdown(self._empty_payload(3))
        self.assertIn("All gates passed", md)
        self.assertIn("0 findings", md)
        self.assertIn("3 providers", md)

    def test_zero_findings_checkmark_emoji(self):
        md = render_markdown(self._empty_payload())
        self.assertIn("\u2705", md)  # checkmark emoji

    def test_zero_findings_single_provider(self):
        md = render_markdown(self._empty_payload(1))
        self.assertIn("1 provider", md)
        # Should NOT say "1 providers"
        self.assertNotIn("1 providers", md)

    def test_zero_findings_no_file_headings(self):
        md = render_markdown(self._empty_payload())
        self.assertNotIn("### `", md)


if __name__ == "__main__":
    unittest.main()
