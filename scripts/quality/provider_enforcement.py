"""Map enabled providers to the status contexts the gate must enforce.

The quality-zero gate is only as strong as the set of GitHub status
contexts it actually waits on. If a provider is wired (enabled in the
profile and run by the reusable workflows) but its QZP-owned ``* Zero``
status context is missing from the resolved required-context set, the
gate would report green while the provider's findings never block.

``expected_provider_contexts`` returns the canonical ``* Zero`` context
each enabled, blocking provider must surface. ``unenforced_providers``
returns the providers whose context is absent from the active required
set so the gate can FAIL closed (treat "wired but absent" as a failure,
not a silent skip).
"""

from __future__ import absolute_import

from typing import Any, Dict, List, Mapping

# Canonical QZP-owned aggregator status contexts, keyed by the
# ``enabled_scanners`` / ``scanners`` provider name. These are the
# whole-codebase strict-zero gates the platform owns end to end — each
# consumes one provider's findings and hard-blocks on any of them.
PROVIDER_ZERO_CONTEXTS: Dict[str, str] = {
    "coverage": "shared-scanner-matrix / Coverage 100 Gate",
    "codecov": "shared-codecov-analytics / Codecov Analytics",
    "qlty": "shared-scanner-matrix / QLTY Zero",
    "sonar": "shared-scanner-matrix / Sonar Zero",
    "codacy": "shared-scanner-matrix / Codacy Zero",
    "semgrep": "shared-scanner-matrix / Semgrep Zero",
    "sentry": "shared-scanner-matrix / Sentry Zero",
    "deepscan": "shared-scanner-matrix / DeepScan Zero",
    "deepsource_visible": "shared-scanner-matrix / DeepSource Visible Zero",
    "codeql": "codeql / CodeQL",
}


def _provider_is_enabled(profile: Mapping[str, Any], provider: str) -> bool:
    """Return whether one provider is wired (enabled) on this profile."""
    enabled_scanners = profile.get("enabled_scanners", {})
    if isinstance(enabled_scanners, Mapping) and bool(
        enabled_scanners.get(provider, False)
    ):
        return True
    # CodeQL enablement lives under its own ``codeql.enabled`` block rather
    # than ``enabled_scanners``; honour it so CodeQL stays enforced.
    if provider == "codeql":
        codeql = profile.get("codeql", {})
        return isinstance(codeql, Mapping) and bool(codeql.get("enabled", False))
    return False


def _provider_is_blocking(profile: Mapping[str, Any], provider: str) -> bool:
    """Return whether one provider's severity should hard-block the gate.

    Only providers with a QZP-owned Zero context (``PROVIDER_ZERO_CONTEXTS``)
    are ever queried here, and they default to ``block``. Informational
    providers such as ``socket_project_report`` have no Zero context and so
    are never demanded as a required check — an explicit ``severity: info``
    on a mapped provider also relaxes it to non-blocking.
    """
    scanners = profile.get("scanners", {})
    if not isinstance(scanners, Mapping):
        return True
    entry = scanners.get(provider)
    if not isinstance(entry, Mapping):
        return True
    return str(entry.get("severity", "block")).strip().lower() == "block"


def expected_provider_contexts(profile: Mapping[str, Any]) -> Dict[str, str]:
    """Return ``{provider: zero_context}`` for every enforced provider.

    A provider is enforced when it is both enabled (wired) and blocking
    (severity ``block``). Informational providers are excluded so the gate
    does not demand a context that, by policy, never fails.
    """
    return {
        provider: context
        for provider, context in PROVIDER_ZERO_CONTEXTS.items()
        if _provider_is_enabled(profile, provider)
        and _provider_is_blocking(profile, provider)
    }


def unenforced_providers(
    profile: Mapping[str, Any],
    active_contexts: List[str],
) -> List[str]:
    """Return findings for enforced providers absent from ``active_contexts``.

    ``active_contexts`` is the resolved required-context list the gate will
    actually wait on for this event. Any enforced provider whose Zero
    context is not present is a silent-pass hole — the provider runs but
    its result can never fail the gate. Returning the provider here lets the
    caller FAIL closed.
    """
    active = {str(item).strip() for item in active_contexts}
    findings: List[str] = []
    for provider, context in sorted(expected_provider_contexts(profile).items()):
        if context not in active:
            findings.append(
                f"{provider}: enabled+blocking but required context "
                f"'{context}' is not enforced"
            )
    return findings
