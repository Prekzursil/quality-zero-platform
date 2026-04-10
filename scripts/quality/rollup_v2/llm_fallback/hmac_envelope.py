"""HMAC envelope encode/decode for LLM cache integrity (per design §5.2 + §B.3.12)."""
from __future__ import absolute_import

import hashlib
import hmac
import json
from typing import Any


def _canonical_json(payload: dict[str, Any]) -> bytes:
    """Canonical JSON: sorted keys, compact separators, UTF-8 encoded."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encode_envelope(payload: dict[str, Any], hmac_key: str) -> dict[str, Any]:
    """Create an HMAC-signed envelope around *payload*.

    Returns ``{"signature": "hmac-sha256:<hex>", "payload": <payload>}``.
    """
    sig = hmac.new(
        hmac_key.encode("utf-8"),
        _canonical_json(payload),
        hashlib.sha256,
    ).hexdigest()
    return {"signature": f"hmac-sha256:{sig}", "payload": payload}


def verify_envelope(envelope: dict[str, Any], hmac_key: str) -> dict[str, Any] | None:
    """Verify the HMAC signature on *envelope*.

    Returns the payload dict if valid, ``None`` if tampered, missing keys,
    or malformed.
    """
    if "signature" not in envelope or "payload" not in envelope:
        return None
    sig_value = envelope["signature"]
    if not isinstance(sig_value, str) or not sig_value.startswith("hmac-sha256:"):
        return None
    expected_hex = sig_value[len("hmac-sha256:"):]
    actual = hmac.new(
        hmac_key.encode("utf-8"),
        _canonical_json(envelope["payload"]),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hex, actual):
        return None
    return envelope["payload"]
