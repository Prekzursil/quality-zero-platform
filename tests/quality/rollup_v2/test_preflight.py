"""Tests for LLM fallback preflight check (per design §B.3.12)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.llm_fallback.preflight import preflight_check


class PreflightCheckTests(unittest.TestCase):
    def test_flag_false_no_op(self):
        """When enable_llm_patches is False, always succeeds regardless of env."""
        result = preflight_check(enable_llm_patches=False, env={})
        self.assertIsNone(result)

    def test_flag_false_with_env_no_op(self):
        result = preflight_check(
            enable_llm_patches=False,
            env={"QZP_LLM_CACHE_HMAC_KEY": "some-key"},
        )
        self.assertIsNone(result)

    def test_flag_true_missing_key_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            preflight_check(enable_llm_patches=True, env={})
        self.assertIn("QZP_LLM_CACHE_HMAC_KEY", str(ctx.exception))
        self.assertIn("--enable-llm-patches", str(ctx.exception))

    def test_flag_true_empty_key_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            preflight_check(
                enable_llm_patches=True,
                env={"QZP_LLM_CACHE_HMAC_KEY": ""},
            )
        self.assertIn("QZP_LLM_CACHE_HMAC_KEY", str(ctx.exception))

    def test_flag_true_present_key_succeeds(self):
        result = preflight_check(
            enable_llm_patches=True,
            env={"QZP_LLM_CACHE_HMAC_KEY": "my-secret-key"},
        )
        self.assertIsNone(result)

    def test_error_message_references_docs(self):
        with self.assertRaises(RuntimeError) as ctx:
            preflight_check(enable_llm_patches=True, env={})
        self.assertIn("docs/llm-fallback-setup.md", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
