"""Corroborator dataclass for canonical findings (per design §A.3.2 + §B.3.4)."""
from __future__ import absolute_import

from dataclasses import dataclass

from scripts.quality.rollup_v2.providers import priority_rank_for


@dataclass(frozen=True, slots=True)
class Corroborator:
    """A per-provider record attached to a canonical Finding.

    Always construct via `Corroborator.from_provider(...)` — direct construction
    with `provider_priority_rank == -1` (the "not looked up" sentinel) raises.
    """
    provider: str
    rule_id: str
    rule_url: str | None
    original_message: str
    provider_priority_rank: int

    def __post_init__(self) -> None:
        if self.provider_priority_rank == -1:
            raise AssertionError(
                "Corroborator.provider_priority_rank was not set. "
                "Use Corroborator.from_provider() instead of direct construction."
            )

    @classmethod
    def from_provider(
        cls,
        provider: str,
        rule_id: str,
        rule_url: str | None,
        original_message: str,
    ) -> "Corroborator":
        """Preferred constructor: looks up the priority rank from PROVIDER_PRIORITY_RANK."""
        return cls(
            provider=provider,
            rule_id=rule_id,
            rule_url=rule_url,
            original_message=original_message,
            provider_priority_rank=priority_rank_for(provider),
        )
