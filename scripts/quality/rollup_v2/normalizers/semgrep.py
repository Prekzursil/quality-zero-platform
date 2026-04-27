"""Semgrep SARIF normalizer (per design §9.1).

Inherits ``parse()`` from :class:`SarifBackedNormalizer`; the only thing that
differs from the CodeQL normalizer is the provider name (used for taxonomy
lookup and corroborator construction).
"""
from __future__ import absolute_import

from scripts.quality.rollup_v2.normalizers._sarif import SarifBackedNormalizer


class SemgrepNormalizer(SarifBackedNormalizer):
    """Normalize Semgrep SARIF output into canonical findings."""

    provider = "Semgrep"
