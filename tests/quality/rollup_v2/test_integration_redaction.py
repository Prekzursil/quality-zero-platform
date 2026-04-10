"""End-to-end integration test: normalizer -> redact -> canonical.json (no secret leaks)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.quality.rollup_v2.test_redaction import _build_test_token_shape
from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer
from scripts.quality.rollup_v2.schema.finding import CATEGORY_GROUP_QUALITY

# Build the token once at module load so the same value is used everywhere.
_LEAKY_TOKEN = _build_test_token_shape()


# Secret values used in the leaky normalizer -- each matches a known redaction pattern.
_OPENAI_KEY = "sk-" + "a" * 40                   # OpenAI sk- pattern (full-match)
_NAMED_SECRET = "verylongsecretvaluegoeshereabcdef"  # named assignment pattern (via MY_SECRET =)


class _LeakyNormalizer(BaseNormalizer):
    provider = "Codacy"

    def parse(self, artifact, repo_root):
        return [
            self._build_finding(
                finding_id="leak-1",
                file="a.py",
                line=1,
                category="broad-except",
                category_group=CATEGORY_GROUP_QUALITY,
                severity="medium",
                primary_message=f"leaked key: {_OPENAI_KEY}",
                rule_id="Pylint_W0703",
                rule_url=None,
                original_message=f"also leaked: token={_LEAKY_TOKEN}",
                context_snippet=f'MY_SECRET = "{_NAMED_SECRET}"',
            )
        ]


class IntegrationRedactionTests(unittest.TestCase):
    def test_no_secret_survives_to_canonical_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "a.py").write_text("x", encoding="utf-8")
            norm = _LeakyNormalizer()
            result = norm.run(artifact=None, repo_root=root)
            serialized = json.dumps([asdict(f) for f in result.findings], default=str)
            for secret in (
                _OPENAI_KEY,
                _LEAKY_TOKEN,
                _NAMED_SECRET,
            ):
                self.assertNotIn(secret, serialized, f"Secret leaked: {secret}")
            self.assertIn("<REDACTED>", serialized)


if __name__ == "__main__":
    unittest.main()
