"""Tests for LLM fallback cache key computation (per design §5.2)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.llm_fallback.cache_key import compute_cache_key


class CacheKeyTests(unittest.TestCase):
    """Determinism, uniqueness, and edge-case tests for compute_cache_key."""

    def _sample_content(self, lines: int = 30) -> str:
        return "\n".join(f"line {i}" for i in range(1, lines + 1))

    def test_same_input_same_key(self):
        content = self._sample_content()
        k1 = compute_cache_key(content, finding_line=15, rule_id="broad-except", category="quality")
        k2 = compute_cache_key(content, finding_line=15, rule_id="broad-except", category="quality")
        self.assertEqual(k1, k2)

    def test_different_content_different_key(self):
        content_a = self._sample_content(30)
        content_b = content_a.replace("line 15", "modified line 15")
        k1 = compute_cache_key(content_a, finding_line=15, rule_id="broad-except", category="quality")
        k2 = compute_cache_key(content_b, finding_line=15, rule_id="broad-except", category="quality")
        self.assertNotEqual(k1, k2)

    def test_different_rule_id_different_key(self):
        content = self._sample_content()
        k1 = compute_cache_key(content, finding_line=15, rule_id="broad-except", category="quality")
        k2 = compute_cache_key(content, finding_line=15, rule_id="unused-import", category="quality")
        self.assertNotEqual(k1, k2)

    def test_different_category_different_key(self):
        content = self._sample_content()
        k1 = compute_cache_key(content, finding_line=15, rule_id="broad-except", category="quality")
        k2 = compute_cache_key(content, finding_line=15, rule_id="broad-except", category="security")
        self.assertNotEqual(k1, k2)

    def test_different_line_different_key(self):
        content = self._sample_content()
        k1 = compute_cache_key(content, finding_line=10, rule_id="r", category="c")
        k2 = compute_cache_key(content, finding_line=20, rule_id="r", category="c")
        self.assertNotEqual(k1, k2)

    def test_line_near_start_of_file(self):
        """Line 2 in a short file: window starts at line 1."""
        content = self._sample_content(5)
        key = compute_cache_key(content, finding_line=2, rule_id="r", category="c")
        self.assertTrue(key)  # no crash, produces a non-empty hash

    def test_line_near_end_of_file(self):
        """Last line: window ends at EOF."""
        content = self._sample_content(5)
        key = compute_cache_key(content, finding_line=5, rule_id="r", category="c")
        self.assertTrue(key)

    def test_line_beyond_file_length(self):
        """Line number past EOF: still produces a deterministic key."""
        content = self._sample_content(3)
        key = compute_cache_key(content, finding_line=100, rule_id="r", category="c")
        self.assertTrue(key)

    def test_single_line_file(self):
        key = compute_cache_key("only one line", finding_line=1, rule_id="r", category="c")
        self.assertTrue(key)

    def test_key_is_hex_sha256(self):
        key = compute_cache_key("abc", finding_line=1, rule_id="r", category="c")
        self.assertEqual(len(key), 64)  # sha256 hex digest is 64 chars
        int(key, 16)  # must be valid hex

    def test_empty_content(self):
        key = compute_cache_key("", finding_line=1, rule_id="r", category="c")
        self.assertTrue(key)


if __name__ == "__main__":
    unittest.main()
