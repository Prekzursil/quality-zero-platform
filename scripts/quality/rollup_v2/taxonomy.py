"""Canonical category taxonomy loader (per design §A.4.4)."""
from __future__ import absolute_import

from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Mapping, Tuple

import yaml

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "taxonomy"


@lru_cache(maxsize=1)
def load_all_taxonomies() -> Mapping[str, Mapping[str, str]]:
    """Load every provider's taxonomy YAML and return {provider: {rule_id: category}}.

    Result is cached for the lifetime of the process. Call `load_all_taxonomies.cache_clear()`
    if the on-disk YAMLs change during tests.
    """
    result: Dict[str, Dict[str, str]] = {}
    if not _CONFIG_DIR.exists():
        return result
    for yaml_path in sorted(_CONFIG_DIR.glob("*.yaml")):
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        provider = data.get("provider")
        mapping = data.get("mapping") or {}
        if not isinstance(provider, str) or not isinstance(mapping, dict):
            raise ValueError(f"invalid taxonomy file: {yaml_path}")
        result[provider] = {str(k): str(v) for k, v in mapping.items()}
    return result


def lookup(provider: str, rule_id: str) -> str | None:
    """Return the canonical category for (provider, rule_id), or None if unmapped."""
    return load_all_taxonomies().get(provider, {}).get(rule_id)


class UnmappedRulesCollector:
    """Accumulates unmapped (provider, rule_id) pairs during a rollup run."""

    def __init__(self) -> None:
        self._counts: Dict[Tuple[str, str], int] = {}

    def record(self, provider: str, rule_id: str) -> None:
        key = (provider, rule_id)
        self._counts[key] = self._counts.get(key, 0) + 1

    def as_list(self) -> List[Dict[str, object]]:
        """Return the collected entries as dicts sorted by (provider, rule_id)."""
        return [
            {"provider": p, "rule_id": r, "count": c}
            for (p, r), c in sorted(self._counts.items())
        ]
