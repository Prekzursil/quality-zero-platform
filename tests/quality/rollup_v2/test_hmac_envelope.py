"""Tests for HMAC envelope encode/decode (per design §5.2 + §B.3.12)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.llm_fallback.hmac_envelope import (
    encode_envelope,
    verify_envelope,
)


class HmacEnvelopeTests(unittest.TestCase):
    """Roundtrip, tamper, and wrong-key tests for HMAC envelope."""

    def test_roundtrip_encode_decode(self):
        payload = {"rule_id": "broad-except", "patch": "--- a/f.py\n+++ b/f.py"}
        key = "test-hmac-key-abc"
        envelope = encode_envelope(payload, key)
        self.assertIn("signature", envelope)
        self.assertIn("payload", envelope)
        self.assertTrue(envelope["signature"].startswith("hmac-sha256:"))
        result = verify_envelope(envelope, key)
        self.assertIsNotNone(result)
        self.assertEqual(result, payload)

    def test_tampered_payload_fails(self):
        payload = {"rule_id": "unused-import", "patch": "diff"}
        key = "my-key"
        envelope = encode_envelope(payload, key)
        # Tamper with the payload
        envelope["payload"]["rule_id"] = "tampered"
        result = verify_envelope(envelope, key)
        self.assertIsNone(result)

    def test_wrong_key_fails(self):
        payload = {"x": 1}
        envelope = encode_envelope(payload, "key-a")
        result = verify_envelope(envelope, "key-b")
        self.assertIsNone(result)

    def test_canonical_json_determinism(self):
        """Same payload with different insertion order produces same signature."""
        key = "det-key"
        payload_a = {"b": 2, "a": 1}
        payload_b = {"a": 1, "b": 2}
        env_a = encode_envelope(payload_a, key)
        env_b = encode_envelope(payload_b, key)
        self.assertEqual(env_a["signature"], env_b["signature"])

    def test_missing_signature_key_returns_none(self):
        result = verify_envelope({"payload": {}}, "key")
        self.assertIsNone(result)

    def test_missing_payload_key_returns_none(self):
        result = verify_envelope({"signature": "hmac-sha256:abc"}, "key")
        self.assertIsNone(result)

    def test_malformed_signature_prefix_returns_none(self):
        payload = {"x": 1}
        key = "k"
        envelope = encode_envelope(payload, key)
        envelope["signature"] = "bad-prefix:" + envelope["signature"].split(":")[1]
        result = verify_envelope(envelope, key)
        self.assertIsNone(result)

    def test_empty_payload_roundtrip(self):
        key = "k"
        envelope = encode_envelope({}, key)
        result = verify_envelope(envelope, key)
        self.assertEqual(result, {})

    def test_nested_payload_roundtrip(self):
        payload = {"a": {"b": [1, 2, 3]}, "c": True}
        key = "nested-key"
        envelope = encode_envelope(payload, key)
        result = verify_envelope(envelope, key)
        self.assertEqual(result, payload)


if __name__ == "__main__":
    unittest.main()
