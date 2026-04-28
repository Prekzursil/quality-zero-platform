"""Cache key computation for LLM fallback patch cache (per design §5.2)."""
from __future__ import absolute_import

import hashlib

_CONTEXT_WINDOW: int = 10  # lines before and after the finding line


def compute_cache_key(
    source_file_content: str,
    finding_line: int,
    rule_id: str,
    category: str,
) -> str:
    """Compute a deterministic SHA-256 cache key for an LLM patch request.

    The key is derived from the surrounding window (10 lines before/after
    *finding_line*), plus *rule_id* and *category*.  This ensures that
    identical source context + rule produces the same cache hit.
    """
    lines = source_file_content.splitlines()
    # finding_line is 1-based; convert to 0-based index
    idx = max(0, finding_line - 1)
    start = max(0, idx - _CONTEXT_WINDOW)
    end = min(len(lines), idx + _CONTEXT_WINDOW + 1)
    window = "\n".join(lines[start:end])
    blob = f"{window}\n{rule_id}\n{category}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
