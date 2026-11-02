#!/usr/bin/env python3
"""Compare a generated ruleset payload with the ruleset GitHub actually enforces.

Every drift check QZP had compared ``generated/rulesets/*.json`` with files
regenerated from the same commit, so it could only ever read green, while the
live rulesets had been edited by hand (measured 2026-10-24: 13 repos whose
declared rulesets required 13-19 retired contexts, enforced with 2). The one
live comparison, inline in ``reusable-ruleset-sync.yml``, read ``rules`` from
``GET /repos/{o}/{r}/rulesets``: the LIST endpoint, which returns no ``rules``
at all, so it would have crashed; it also assumed the required-checks rule was
always ``rules[1]``.

This module fetches the full ruleset (``GET /repos/{o}/{r}/rulesets/{id}``,
after reading EVERY page of the list), finds it by name, and compares rules by
TYPE, never by position:

* ``target``, ``enforcement`` and ``conditions`` (which refs it applies to);
* the SET of rule types: a declared rule missing live (``deletion``,
  ``non_fast_forward``...) or a live rule nobody declared is drift;
* every parameter the declaration sets on a rule (``strict_required_status_
  checks_policy``, the review requirements...). A parameter GitHub returns
  that the declaration does not set is GitHub's default, not drift;
* the required status-check contexts (as a set: order does not matter to GitHub);
* the ``code_scanning`` merge-protection tools and thresholds;
* the bypass actors, when the token can see them (GitHub omits the key
  otherwise, which is reported as unverifiable rather than as drift).

Lists are compared order-insensitively throughout: nothing a ruleset enforces
depends on list order, and the API reorders them.

Exit codes: ``0`` in sync, ``1`` drift, ``2`` the comparison could not be made
(missing ruleset, API or input error) - never reported as in sync.
"""

from __future__ import absolute_import

import argparse
import json
import subprocess  # nosec B404 # noqa: S404 - gh CLI wrapper; argv is fixed
import sys
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

EXIT_IN_SYNC = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2

#: Top-level ruleset fields compared as a whole.
COMPARED_FIELDS = ("target", "enforcement", "conditions")

#: The list parameter each rule type's dedicated set comparison owns.
SET_COMPARED_PARAMETERS = {
    "required_status_checks": "required_status_checks",
    "code_scanning": "code_scanning_tools",
}

ProcessRunner = Callable[..., "subprocess.CompletedProcess[str]"]


class LiveRulesetError(RuntimeError):
    """The live ruleset could not be read, so nothing can be said about drift."""


def _rules_of_type(payload: Mapping[str, Any], rule_type: str) -> List[Mapping[str, Any]]:
    """Return every rule of ``rule_type`` in a ruleset payload."""
    rules = payload.get("rules") or []
    return [rule for rule in rules if isinstance(rule, Mapping) and rule.get("type") == rule_type]


def _parameters(rule: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a rule's ``parameters`` mapping (empty when absent)."""
    parameters = rule.get("parameters")
    return parameters if isinstance(parameters, Mapping) else {}


def canonical(value: Any) -> str:
    """Render ``value`` as JSON with mapping keys AND list items sorted."""
    if isinstance(value, Mapping):
        items = sorted((str(key), canonical(item)) for key, item in value.items())
        return "{" + ",".join(f"{json.dumps(key)}:{item}" for key, item in items) + "}"
    if isinstance(value, list):
        return "[" + ",".join(sorted(canonical(item) for item in value)) + "]"
    return json.dumps(value)


def rule_types(payload: Mapping[str, Any]) -> FrozenSet[str]:
    """Return the type of every rule in a ruleset payload."""
    rules = payload.get("rules") or []
    return frozenset(str(rule["type"]) for rule in rules if isinstance(rule, Mapping) and rule.get("type"))


def merged_parameters(payload: Mapping[str, Any], rule_type: str) -> Dict[str, Any]:
    """Return the parameters of every ``rule_type`` rule, merged in order."""
    merged: Dict[str, Any] = {}
    for rule in _rules_of_type(payload, rule_type):
        merged.update(_parameters(rule))
    return merged


def required_contexts(payload: Mapping[str, Any]) -> FrozenSet[str]:
    """Return the required status-check contexts across every required-checks rule."""
    contexts = set()
    for rule in _rules_of_type(payload, "required_status_checks"):
        for check in _parameters(rule).get("required_status_checks") or []:
            if isinstance(check, Mapping) and str(check.get("context", "")).strip():
                contexts.add(str(check["context"]).strip())
    return frozenset(contexts)


def code_scanning_tools(payload: Mapping[str, Any]) -> FrozenSet[Tuple[str, str, str]]:
    """Return ``(tool, alerts_threshold, security_alerts_threshold)`` for every tool."""
    tools = set()
    for rule in _rules_of_type(payload, "code_scanning"):
        for tool in _parameters(rule).get("code_scanning_tools") or []:
            if isinstance(tool, Mapping):
                tools.add(
                    (
                        str(tool.get("tool", "")),
                        str(tool.get("alerts_threshold", "")),
                        str(tool.get("security_alerts_threshold", "")),
                    )
                )
    return frozenset(tools)


def bypass_actors(payload: Mapping[str, Any]) -> Optional[FrozenSet[Tuple[str, str, str]]]:
    """Return ``(actor_type, actor_id, bypass_mode)`` per actor, or ``None`` when hidden.

    GitHub omits ``bypass_actors`` for a token that may not see them; ``None``
    keeps "not visible" distinct from "there are none". An ``OrganizationAdmin``
    actor's id is ignored by the API, so it is not compared either.
    """
    actors = payload.get("bypass_actors")
    if not isinstance(actors, list):
        return None
    return frozenset(
        (
            str(actor.get("actor_type", "")),
            "" if actor.get("actor_type") == "OrganizationAdmin" else str(actor.get("actor_id", "")),
            str(actor.get("bypass_mode", "")),
        )
        for actor in actors
        if isinstance(actor, Mapping)
    )


def _describe_set_drift(label: str, expected: FrozenSet[Any], live: FrozenSet[Any]) -> List[str]:
    """Describe what one comparable set is missing and what it has extra."""
    findings = []
    missing = sorted(expected - live)
    extra = sorted(live - expected)
    if missing:
        findings.append(f"{label}: declared but not live: {missing}")
    if extra:
        findings.append(f"{label}: live but not declared: {extra}")
    return findings


def _rule_parameter_drift(expected: Mapping[str, Any], live: Mapping[str, Any]) -> List[str]:
    """Describe each parameter a rule present on both sides declares differently live."""
    findings = []
    for rule_type in sorted(rule_types(expected) & rule_types(live)):
        declared = merged_parameters(expected, rule_type)
        actual = merged_parameters(live, rule_type)
        for key in sorted(declared):
            if key == SET_COMPARED_PARAMETERS.get(rule_type):
                continue
            if canonical(declared[key]) != canonical(actual.get(key)):
                findings.append(f"rule {rule_type}: {key} declared {declared[key]!r}, live {actual.get(key)!r}")
    return findings


def compare(expected: Mapping[str, Any], live: Mapping[str, Any]) -> Tuple[List[str], List[str]]:
    """Return ``(drift, notes)``: findings that differ, and what could not be verified."""
    drift: List[str] = []
    notes: List[str] = []
    for field in COMPARED_FIELDS:
        if canonical(expected.get(field)) != canonical(live.get(field)):
            drift.append(f"{field}: declared {expected.get(field)!r}, live {live.get(field)!r}")
    drift.extend(_describe_set_drift("rules", rule_types(expected), rule_types(live)))
    drift.extend(_rule_parameter_drift(expected, live))
    drift.extend(_describe_set_drift("required checks", required_contexts(expected), required_contexts(live)))
    drift.extend(_describe_set_drift("code scanning", code_scanning_tools(expected), code_scanning_tools(live)))
    expected_actors = bypass_actors(expected) or frozenset()
    live_actors = bypass_actors(live)
    if live_actors is None:
        notes.append("bypass actors: not visible with this token, so they were not compared")
    else:
        drift.extend(_describe_set_drift("bypass actors", expected_actors, live_actors))
    return drift, notes


def decode_documents(text: str) -> List[Any]:
    """Decode EVERY top-level JSON document in ``text``.

    ``gh api --paginate`` prints each page as its own JSON document, one after
    another, and ``json.loads`` rejects everything after the first.
    """
    decoder = json.JSONDecoder()
    documents: List[Any] = []
    index = 0
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index == len(text):
            return documents
        document, index = decoder.raw_decode(text, index)
        documents.append(document)


def _gh_documents(args: Sequence[str], *, runner: ProcessRunner) -> List[Any]:
    """Run ``gh api`` and decode every JSON document it printed (one per page).

    Raises ``LiveRulesetError`` on a failed call, unparseable output, or no
    output at all.
    """
    completed = runner(["gh", "api", *args], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        tail = " ".join(str(completed.stderr).strip().splitlines()[-1:])
        raise LiveRulesetError(f"`gh api {' '.join(args)}` exited {completed.returncode}: {tail}")
    try:
        documents = decode_documents(str(completed.stdout or ""))
    except ValueError as exc:
        raise LiveRulesetError(f"`gh api {' '.join(args)}` returned unparseable JSON: {exc}") from exc
    if not documents:
        raise LiveRulesetError(f"`gh api {' '.join(args)}` returned no JSON")
    return documents


def fetch_live_ruleset(repo_slug: str, name: str, *, runner: ProcessRunner = subprocess.run) -> Mapping[str, Any]:
    """Find the ruleset called ``name`` and return its FULL payload (with ``rules``)."""
    listing: List[Any] = []
    for page in _gh_documents([f"repos/{repo_slug}/rulesets", "--paginate"], runner=runner):
        if not isinstance(page, list):
            raise LiveRulesetError(f"the ruleset list for {repo_slug} is not a list")
        listing.extend(page)
    match = next((item for item in listing if isinstance(item, Mapping) and item.get("name") == name), None)
    if match is None:
        raise LiveRulesetError(f"no live ruleset named {name!r} on {repo_slug}")
    documents = _gh_documents([f"repos/{repo_slug}/rulesets/{match.get('id')}"], runner=runner)
    if len(documents) != 1 or not isinstance(documents[0], Mapping):
        raise LiveRulesetError(f"ruleset {match.get('id')} on {repo_slug} is not an object")
    return documents[0]


def _load_json(path: str) -> Mapping[str, Any]:
    """Read one JSON object from ``path``."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise ValueError(f"{path} does not hold a JSON object")
    return document


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description="Compare a generated ruleset with the live one, or a committed copy.")
    parser.add_argument("--expected", required=True, help="The generated ruleset payload (JSON).")
    parser.add_argument("--committed", default="", help="Also require this committed payload to equal --expected.")
    parser.add_argument("--repo-slug", default="", help="owner/name whose LIVE ruleset to compare.")
    return parser


def main(argv: Optional[Sequence[str]] = None, *, runner: ProcessRunner = subprocess.run) -> int:
    """Run the requested comparisons and print one verdict line per finding."""
    args = _build_parser().parse_args(argv)
    try:
        expected = _load_json(args.expected)
        committed = _load_json(args.committed) if args.committed else None
    except (OSError, ValueError) as exc:
        print(f"ERROR ruleset-drift: cannot read the payloads: {exc}", file=sys.stderr)
        return EXIT_ERROR
    drift: List[str] = []
    if committed is not None and committed != expected:
        drift.append(f"committed payload {args.committed} differs from the regenerated one")
    if args.repo_slug:
        try:
            live = fetch_live_ruleset(args.repo_slug, str(expected.get("name", "")), runner=runner)
        except LiveRulesetError as exc:
            print(
                f"ERROR ruleset-drift: the live ruleset could not be read, so drift is UNKNOWN: {exc}", file=sys.stderr
            )
            return EXIT_ERROR
        live_drift, notes = compare(expected, live)
        drift.extend(live_drift)
        for note in notes:
            print(f"NOTE ruleset-drift: {note}")
    for finding in drift:
        print(f"DRIFT ruleset-drift: {finding}")
    if drift:
        return EXIT_DRIFT
    print("PASS ruleset-drift: the declared ruleset matches what was compared.")
    return EXIT_IN_SYNC


if __name__ == "__main__":
    raise SystemExit(main())
