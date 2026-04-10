"""Fail-fast preflight for LLM fallback feature gate (per design §B.3.12)."""
from __future__ import absolute_import

from typing import Mapping


def preflight_check(
    *,
    enable_llm_patches: bool,
    env: Mapping[str, str],
) -> None:
    """Raise ``RuntimeError`` if ``--enable-llm-patches`` is set but HMAC key is missing.

    No-op when the flag is ``False``.
    """
    if not enable_llm_patches:
        return None
    if not env.get("QZP_LLM_CACHE_HMAC_KEY"):
        raise RuntimeError(
            "FATAL: --enable-llm-patches is set but QZP_LLM_CACHE_HMAC_KEY secret "
            "is not provisioned. Either provision the secret (see "
            "docs/llm-fallback-setup.md) or remove --enable-llm-patches."
        )
    return None
