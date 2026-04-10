"""Provider priority ranking for canonical-finding merges (per design §A.4.3)."""
from __future__ import absolute_import

from typing import Final, Mapping

UNKNOWN_PROVIDER_RANK: Final[int] = 99

PROVIDER_PRIORITY_RANK: Final[Mapping[str, int]] = {
    "CodeQL": 0,
    "SonarCloud": 1,
    "Codacy": 2,
    "DeepSource": 3,
    "Semgrep": 4,
    "QLTY": 5,
    "DeepScan": 6,
    "Sentry": UNKNOWN_PROVIDER_RANK,
    "Chromatic": UNKNOWN_PROVIDER_RANK,
    "Applitools": UNKNOWN_PROVIDER_RANK,
}


def priority_rank_for(provider: str) -> int:
    """Return the priority rank for a provider; UNKNOWN_PROVIDER_RANK for unknown."""
    return PROVIDER_PRIORITY_RANK.get(provider, UNKNOWN_PROVIDER_RANK)
