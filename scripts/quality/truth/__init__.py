"""Truthful-gate subsystem (TG-program).

This package hosts the "truth model" gates that make the fleet's quality
state *truthful* — observed live, never believed from a stale dashboard
scrape. TG-2 (``preflight``) is the first ship: it turns the campaign's
master blocker — rotated SaaS tokens — loud instead of silent before any
adapter relies on a live dashboard read.

See ``docs/plans/2026-06-01-truthful-gate-subsystem-design.md`` for the
parent design and ``docs/plans/2026-06-01-truthful-gate-tg2-token-preflight-plan.md``
for the TG-2 contract.
"""

from __future__ import absolute_import

__all__ = ["preflight"]
