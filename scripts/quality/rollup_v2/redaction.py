"""Secret redaction for quality-rollup-v2 canonical findings (per design B.1)."""
from __future__ import absolute_import

import re
from typing import Final

_NAMED_ASSIGNMENT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"([A-Za-z_][A-Za-z0-9_]*(?:_?(?:KEY|TOKEN|SECRET|PASSWORD|PASS|PWD|DSN|API[_-]?KEY|"
        r"ACCESS[_-]?TOKEN|REFRESH[_-]?TOKEN|CLIENT[_-]?SECRET|PRIVATE[_-]?KEY|AUTH))"
        r"\s*[=:]\s*)"
        r"""(["']?)([^"'\s,;]{8,})\2""",
        re.IGNORECASE,
    ),
)

_FULL_MATCH_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # Bare JWTs
    re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"),
    # PEM blocks
    re.compile(
        r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?"
        r"(?:PRIVATE\s+KEY|CERTIFICATE|ENCRYPTED\s+PRIVATE\s+KEY)-----"
        r"[\s\S]{16,}?"
        r"-----END\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?"
        r"(?:PRIVATE\s+KEY|CERTIFICATE|ENCRYPTED\s+PRIVATE\s+KEY)-----"
    ),
    # OpenAI sk-
    re.compile(r"\bsk-[A-Za-z0-9_\-]{32,}\b"),
    # GitHub PATs
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"),
    # AWS access key IDs
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # Authorization: Bearer
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([A-Za-z0-9._\-]{16,})"),
    # Slack bot/user/app/legacy tokens
    re.compile(r"\bxox[baprs]-[0-9]+-[0-9]+-[A-Za-z0-9]{20,}\b"),
    # Stripe live/test keys
    re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{24,}\b"),
    # GCP service account: JSON-escaped PEM private_key field
    re.compile(r'"private_key"\s*:\s*"[^"]{16,}"'),
    # Azure SAS: sig=<urlsafe-base64>
    re.compile(r"(?i)([?&]sig=)([A-Za-z0-9%+/=\-_]{20,})"),
)

REDACTED: Final[str] = "<REDACTED>"


def redact_secrets(text: str) -> str:
    """Return ``text`` with all known secret patterns replaced by REDACTED.

    Idempotent: ``redact_secrets(redact_secrets(x)) == redact_secrets(x)``.
    """
    if not text:
        return text
    result = text
    # Named assignments: keep prefix + quote, replace value, preserve quote pairing.
    for pattern in _NAMED_ASSIGNMENT_PATTERNS:
        result = pattern.sub(rf"\1\2{REDACTED}\2", result)
    # Full-match patterns: replace the entire match with REDACTED.
    for pattern in _FULL_MATCH_PATTERNS:
        result = pattern.sub(REDACTED, result)
    return result
