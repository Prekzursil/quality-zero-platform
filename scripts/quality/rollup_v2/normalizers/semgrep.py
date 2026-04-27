"""Semgrep SARIF normalizer (per design §9.1).

Subclasses ``SarifJsonNormalizer`` (defined in ``_sarif.py``) — the
shared base does all the dict/str/bytes artifact dispatch + 50MB size
guard. Semgrep-specific taxonomy mapping fires inside ``parse_sarif``
via ``taxonomy.lookup("Semgrep", rule_id)``.
"""

from __future__ import absolute_import

from scripts.quality.rollup_v2.normalizers._sarif import SarifJsonNormalizer


class SemgrepNormalizer(SarifJsonNormalizer):
    """Normalize Semgrep SARIF output into canonical findings."""

    provider = "Semgrep"
