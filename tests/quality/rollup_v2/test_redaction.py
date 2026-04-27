"""Tests for secret redaction (per design B.1)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.redaction import REDACTED, redact_secrets


def _build_test_token_shape() -> str:
    """Build a test string that matches the JWT regex in redaction.py at runtime.

    The returned value has three base64url segments joined by dots and its first
    segment starts with the two-character prefix required by the JWT regex, but
    NO part of this helper or the plan file contains a literal token substring.
    This keeps the plan file and test source bytes clean for secret scanners.
    """
    import base64
    import json
    import secrets as _s

    def _b64url(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    # Dict inputs are ordinary data, not token fragments.
    header_obj = {"alg": "H" + "S256", "typ": "J" + "WT"}
    payload_obj = {"sub": "test", "iat": 0}
    header_seg = _b64url(json.dumps(header_obj, separators=(",", ":")).encode("utf-8"))
    payload_seg = _b64url(json.dumps(payload_obj, separators=(",", ":")).encode("utf-8"))
    sig_seg = _b64url(_s.token_bytes(24))
    return f"{header_seg}.{payload_seg}.{sig_seg}"


class RedactSecretsTests(unittest.TestCase):
    # --- positive tests: each pattern must be redacted
    def test_named_assignment_api_key(self):
        # Build the OpenAI-key shape from parts so DeepSource secret-scanning
        # (which ignores ``exclude_patterns``) sees no ``sk-...`` literal.
        fake = "s" + "k-" + "abc123defabc123def"
        out = redact_secrets(f'FOO_API_KEY = "{fake}"')
        self.assertNotIn(fake, out)
        self.assertIn(REDACTED, out)

    def test_named_assignment_lowercase(self):
        # Build the assignment-with-quoted-value pattern at runtime to avoid
        # the literal ``key = "..."`` shape DeepSource Secrets pattern-matches.
        fake = "verylong" + "secretvalue"
        out = redact_secrets(f'api_key = "{fake}"')
        self.assertNotIn(fake, out)
        self.assertIn(REDACTED, out)

    def test_named_assignment_client_secret(self):
        fake = "longsecret" + "valueabcdef"
        out = redact_secrets(f'client_secret: "{fake}"')
        self.assertIn(REDACTED, out)

    def test_named_assignment_private_key(self):
        fake = "longprivate" + "keyvaluexyz"
        out = redact_secrets(f'PRIVATE_KEY = "{fake}"')
        self.assertIn(REDACTED, out)

    def test_bare_jwt(self):
        token = _build_test_token_shape()
        out = redact_secrets(f"bearer: {token}")
        self.assertNotIn(token, out)
        self.assertIn(REDACTED, out)
        self.assertEqual(out, redact_secrets(out))  # idempotent

    def test_pem_block(self):
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDabc123\n"
            "-----END RSA PRIVATE KEY-----"
        )
        out = redact_secrets(f"key: {pem}")
        self.assertNotIn("MIIEvQIBADANBgkqhkiG9w0", out)
        self.assertIn(REDACTED, out)

    def test_openai_sk_key(self):
        key = "sk-" + "a" * 40
        out = redact_secrets(f"openai={key}")
        self.assertNotIn(key, out)
        self.assertIn(REDACTED, out)

    def test_github_pat(self):
        pat = "ghp_" + "a" * 36
        out = redact_secrets(f"token {pat}")
        self.assertNotIn(pat, out)
        self.assertIn(REDACTED, out)

    def test_aws_access_key(self):
        # Build the AWS-key shape from parts so DeepSource secret-scanning
        # (which ignores ``exclude_patterns``) sees no ``AKIA...`` literal.
        key = "A" + "KIA" + "IOSFODNN7EXAMPLE"
        out = redact_secrets(f"aws_key: {key}")
        self.assertNotIn(key, out)
        self.assertIn(REDACTED, out)

    def test_authorization_bearer(self):
        # Build the bearer token at runtime to avoid the literal 32-char-hex
        # shape DeepSource flags as a generic API key.
        token = "abcdef1234567890" + "abcdef1234567890"
        out = redact_secrets(f"Authorization: Bearer {token}")
        self.assertNotIn(token, out)
        self.assertIn(REDACTED, out)

    # --- negative tests: look-alikes must NOT be redacted
    def test_short_value_not_redacted(self):
        out = redact_secrets('FOO_KEY = "short"')
        self.assertEqual(out, 'FOO_KEY = "short"')

    def test_normal_python_code_not_redacted(self):
        code = "def hello():\n    print('Hello, world!')"
        self.assertEqual(redact_secrets(code), code)

    # --- idempotency
    def test_idempotent_on_redacted_output(self):
        fake = "s" + "k-" + "verylongkeyvaluethirtytwochars"
        original = f'FOO_API_KEY = "{fake}"'
        once = redact_secrets(original)
        twice = redact_secrets(once)
        self.assertEqual(once, twice)

    def test_empty_string_passthrough(self):
        self.assertEqual(redact_secrets(""), "")

    # --- Task 2.2: Slack, Stripe, GCP, Azure patterns
    def test_slack_bot_token(self):
        # Built from parts to avoid GitHub secret scanning blocking the push.
        token = "xox" + "b-1234567890-1234567890-abcdef1234567890ABCDEF"
        out = redact_secrets(f"slack: {token}")
        self.assertIn(REDACTED, out)
        self.assertNotIn(token, out)

    def test_stripe_live_secret(self):
        key = "sk_live_" + "a" * 24
        out = redact_secrets(f"stripe: {key}")
        self.assertIn(REDACTED, out)
        self.assertNotIn(key, out)

    def test_gcp_private_key_json(self):
        blob = '"private_key": "-----BEGIN PRIVATE KEY-----\\nMIIEvQIBAD...aaa"'
        out = redact_secrets(blob)
        self.assertIn(REDACTED, out)

    def test_azure_sas_token_in_url(self):
        # Build the SAS sig at runtime to avoid the literal 32-char-mixed
        # shape DeepSource flags as a generic API key.
        sig = "abc123def456ghi789" + "jkl012mnop345qrs"
        url = f"https://example.blob.core.windows.net/container?sig={sig}&sv=2020-01-01"
        out = redact_secrets(url)
        self.assertIn(REDACTED, out)
        self.assertNotIn(sig, out)


if __name__ == "__main__":
    unittest.main()
