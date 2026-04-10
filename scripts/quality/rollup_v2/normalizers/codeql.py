"""CodeQL SARIF normalizer (per design §9.2).

Delegates to the shared SARIF parser in ``_sarif.py``, applying CodeQL-specific
taxonomy mapping via ``taxonomy.lookup("CodeQL", rule_id)``.
"""
from __future__ import absolute_import

import json
from pathlib import Path
from typing import Any, Iterable

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer
from scripts.quality.rollup_v2.normalizers._sarif import (
    SarifTooLargeError,
    check_sarif_size,
    parse_sarif,
)
from scripts.quality.rollup_v2.schema.finding import Finding


class CodeQLNormalizer(BaseNormalizer):
    """Normalize CodeQL SARIF output into canonical findings."""

    provider = "CodeQL"

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        """Parse a CodeQL SARIF artifact.

        ``artifact`` may be:
        - A ``dict`` (already-parsed SARIF JSON)
        - A ``str`` (raw SARIF JSON text)
        - A ``bytes`` (raw SARIF JSON bytes)
        """
        if isinstance(artifact, (str, bytes)):
            check_sarif_size(artifact)
            data = json.loads(artifact)
        elif isinstance(artifact, dict):
            data = artifact
        else:
            return []
        return parse_sarif(data, self.provider, repo_root, self)
