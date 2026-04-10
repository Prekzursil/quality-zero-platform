"""Tests for LLM fallback budget guard (per design §5.2)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.llm_fallback.budget import BudgetGuard


class BudgetGuardTests(unittest.TestCase):
    def test_default_max_is_10(self):
        guard = BudgetGuard()
        self.assertEqual(guard.remaining(), 10)

    def test_allows_up_to_max(self):
        guard = BudgetGuard(max_patches=3)
        self.assertTrue(guard.can_proceed())
        guard.record_call()
        self.assertTrue(guard.can_proceed())
        guard.record_call()
        self.assertTrue(guard.can_proceed())
        guard.record_call()
        # After 3 calls, should be exhausted
        self.assertFalse(guard.can_proceed())

    def test_blocks_after_max(self):
        guard = BudgetGuard(max_patches=1)
        guard.record_call()
        self.assertFalse(guard.can_proceed())

    def test_remaining_decrements(self):
        guard = BudgetGuard(max_patches=5)
        self.assertEqual(guard.remaining(), 5)
        guard.record_call()
        self.assertEqual(guard.remaining(), 4)
        guard.record_call()
        self.assertEqual(guard.remaining(), 3)

    def test_remaining_never_negative(self):
        guard = BudgetGuard(max_patches=1)
        guard.record_call()
        # Budget exhausted: remaining stays at 0, record_call raises
        self.assertEqual(guard.remaining(), 0)
        with self.assertRaises(RuntimeError):
            guard.record_call()
        self.assertEqual(guard.remaining(), 0)

    def test_record_call_raises_when_exhausted(self):
        guard = BudgetGuard(max_patches=0)
        with self.assertRaises(RuntimeError):
            guard.record_call()

    def test_custom_max(self):
        guard = BudgetGuard(max_patches=50)
        self.assertEqual(guard.remaining(), 50)
        for _ in range(50):
            self.assertTrue(guard.can_proceed())
            guard.record_call()
        self.assertFalse(guard.can_proceed())

    def test_zero_max_immediately_blocked(self):
        guard = BudgetGuard(max_patches=0)
        self.assertFalse(guard.can_proceed())
        self.assertEqual(guard.remaining(), 0)


if __name__ == "__main__":
    unittest.main()
