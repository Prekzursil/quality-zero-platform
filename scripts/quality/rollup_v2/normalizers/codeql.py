"""CodeQL SARIF normalizer (per design §9.2).

Subclasses ``SarifJsonNormalizer`` (defined in ``_sarif.py``) — the
shared base does all the dict/str/bytes artifact dispatch + 50MB size
guard. CodeQL-specific taxonomy mapping fires inside ``parse_sarif``
via ``taxonomy.lookup("CodeQL", rule_id)``.
"""

from __future__ import absolute_import

from scripts.quality.rollup_v2.normalizers._sarif import SarifJsonNormalizer


class CodeQLNormalizer(SarifJsonNormalizer):
    """Normalize CodeQL SARIF output into canonical findings."""

    provider = "CodeQL"
