#!/usr/bin/env python3
"""canonical.json + profile reading for the Layer-1 ratchet gate.

Pure helpers that turn the rollup's ``canonical.json`` (provider totals,
measured/errored providers) and a resolved profile (expected providers) into
the per-provider inputs the gate's state machine consumes. Kept in their own
module so the gate stays under the file-complexity ceiling without changing
the public surface (``ratchet_gate`` re-exports every name here).
"""

from __future__ import absolute_import

from typing import Any, Dict, List, Mapping, Set, Tuple

from scripts.quality.ratchet_diff import is_new_code_finding

# Exact provider literal strings emitted by the rollup_v2 normalizers
# (BaseNormalizer.provider on each normalizer; verified against
# scripts/quality/rollup_v2/normalizers/*.py). These are the ONLY valid
# keys under ratchet.json.providers.<name>.
KNOWN_PROVIDERS: Tuple[str, ...] = (
    "Applitools",
    "Chromatic",
    "Codacy",
    "CodeQL",
    "Coverage",
    "DeepScan",
    "DeepSource",
    "Dependabot",
    "QLTY",
    "QualitySecrets",
    "Semgrep",
    "Sentry",
    "SonarCloud",
)

# Maps a required-context substring -> the canonical provider literal.
# Used to derive the "expected providers" set from a resolved profile's
# required_contexts so an absent provider is recognised as UNMEASURED
# (block-lower) rather than a genuine zero.
CONTEXT_PROVIDER_HINTS: Tuple[Tuple[str, str], ...] = (
    ("Sonar", "SonarCloud"),
    ("SonarCloud", "SonarCloud"),
    ("Codacy", "Codacy"),
    ("CodeQL", "CodeQL"),
    ("DeepScan", "DeepScan"),
    ("DeepSource", "DeepSource"),
    ("Semgrep", "Semgrep"),
    ("Sentry", "Sentry"),
    ("Dependency", "Dependabot"),
    ("Dependabot", "Dependabot"),
    ("qlty", "QLTY"),
    ("QLTY", "QLTY"),
    ("Coverage", "Coverage"),
    ("Chromatic", "Chromatic"),
    ("Applitools", "Applitools"),
)


def read_provider_totals(canonical: Mapping[str, Any]) -> Dict[str, int]:
    """Return ``{provider: total}`` from ``provider_summaries``.

    ``total`` is corroborator-counted (a multi-provider deduped finding
    increments each of its providers), which is exactly what we want for a
    per-provider ceiling that mirrors each dashboard. We deliberately do NOT
    gate on ``sum(totals)`` -- it is not equal to ``total_findings``.
    """
    totals: Dict[str, int] = {}
    for summary in canonical.get("provider_summaries", []):
        provider = str(summary.get("provider", ""))
        if summary.get("status") == "not-configured":
            continue
        if provider:
            totals[provider] = int(summary.get("total", 0))
    return totals


def measured_providers_from_canonical(
        canonical: Mapping[str, Any]) -> Set[str]:
    """Providers that produced at least one corroborator this run.

    Presence in ``provider_summaries`` (with status != not-configured) means
    the lane ran and emitted findings. A provider that ran clean (0 findings)
    will NOT appear here -- that ambiguity is resolved by the
    ``expected_providers`` set + ``normalizer_errors`` (see ``classify``).
    """
    measured: Set[str] = set()
    for summary in canonical.get("provider_summaries", []):
        if summary.get("status") == "not-configured":
            continue
        provider = str(summary.get("provider", ""))
        if provider:
            measured.add(provider)
    return measured


def providers_with_errors(canonical: Mapping[str, Any]) -> Set[str]:
    """Providers that recorded a normalizer error this run (treat as UNMEASURED)."""
    errored: Set[str] = set()
    for err in canonical.get("normalizer_errors", []):
        provider = str(err.get("provider", "")).strip()
        # normalizer_errors may carry the lowercase lane key; map both forms.
        for known in KNOWN_PROVIDERS:
            if provider and (provider == known
                             or provider.lower() == known.lower()):
                errored.add(known)
    return errored


def _required_contexts(profile: Mapping[str, Any]) -> List[str]:
    """Collect the required-context strings declared by a resolved profile."""
    contexts: List[str] = []
    raw = profile.get("required_contexts", {})
    if isinstance(raw, Mapping):
        for bucket in ("always", "target", "pull_request_only"):
            value = raw.get(bucket, [])
            if isinstance(value, list):
                contexts.extend(str(item) for item in value)
    elif isinstance(raw, list):
        contexts.extend(str(item) for item in raw)
    contexts.extend(
        str(item) for item in profile.get("active_required_contexts", []))
    return contexts


def expected_providers_from_profile(profile: Mapping[str, Any]) -> Set[str]:
    """Derive the providers a healthy run is EXPECTED to measure.

    Pulled from the resolved profile's ``required_contexts`` (always/target
    lanes). Any provider in this set that is absent from canonical's
    ``provider_summaries`` and not in ``normalizer_errors`` is UNMEASURED ->
    the gate holds the ceiling and fails-closed rather than lowering to 0.
    """
    expected: Set[str] = set()
    for context in _required_contexts(profile):
        for needle, provider in CONTEXT_PROVIDER_HINTS:
            if needle in context:
                expected.add(provider)
    return expected


def count_new_code(canonical: Mapping[str, Any],
                   added: Mapping[str, Set[int]]) -> Dict[str, int]:
    """Return ``{provider: new_code_finding_count}`` over all canonical findings."""
    new_by_provider: Dict[str, int] = dict.fromkeys(KNOWN_PROVIDERS, 0)
    for finding in canonical.get("findings", []):
        if not is_new_code_finding(finding, added):
            continue
        for corroborator in finding.get("corroborators", []):
            provider = str(corroborator.get("provider", ""))
            if provider in new_by_provider:
                new_by_provider[provider] += 1
    return new_by_provider
