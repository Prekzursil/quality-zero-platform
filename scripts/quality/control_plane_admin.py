#!/usr/bin/env python3
from __future__ import absolute_import

import argparse
from pathlib import Path
import sys
from typing import Any, Dict

import yaml  # type: ignore[import-untyped]

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _load_yaml(path: Path) -> Dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected mapping at {path}")
    return payload


def _write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def enroll_repo(*, repo_root: Path, repo_slug: str, profile_id: str, stack: str, rollout: str, default_branch: str) -> None:
    inventory_path = repo_root / "inventory" / "repos.yml"
    inventory = _load_yaml(inventory_path)
    inventory.setdefault("version", 1)
    repos = inventory.setdefault("repos", [])
    if not any(item.get("slug") == repo_slug for item in repos):
        repos.append(
            {
                "slug": repo_slug,
                "profile": profile_id,
                "rollout": rollout,
                "default_branch": default_branch,
            }
        )
    _write_yaml(inventory_path, inventory)

    profile_path = repo_root / "profiles" / "repos" / f"{profile_id}.yml"
    if not profile_path.exists():
        _write_yaml(profile_path, {"slug": repo_slug, "stack": stack})


def _profile_path(repo_root: Path, profile_id: str) -> Path:
    return repo_root / "profiles" / "repos" / f"{profile_id}.yml"


def set_scanner(*, repo_root: Path, profile_id: str, scanner: str, enabled: bool) -> None:
    path = _profile_path(repo_root, profile_id)
    payload = _load_yaml(path)
    enabled_scanners = payload.setdefault("enabled_scanners", {})
    enabled_scanners[scanner] = bool(enabled)
    _write_yaml(path, payload)


def set_issue_policy(*, repo_root: Path, profile_id: str, mode: str) -> None:
    path = _profile_path(repo_root, profile_id)
    payload = _load_yaml(path)
    payload["issue_policy"] = {"mode": mode}
    _write_yaml(path, payload)


def set_coverage_mode(*, repo_root: Path, profile_id: str, event_name: str, mode: str) -> None:
    path = _profile_path(repo_root, profile_id)
    payload = _load_yaml(path)
    coverage = payload.setdefault("coverage", {})
    assert_mode = coverage.setdefault("assert_mode", {})
    assert_mode[event_name] = mode
    _write_yaml(path, payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mutate inventory or profile YAML for admin PR workflows.")
    parser.add_argument("--repo-root", default=".")
    subparsers = parser.add_subparsers(dest="command", required=True)

    enroll = subparsers.add_parser("enroll-repo")
    enroll.add_argument("--repo-slug", required=True)
    enroll.add_argument("--profile-id", required=True)
    enroll.add_argument("--stack", required=True)
    enroll.add_argument("--rollout", default="phase2-wave0")
    enroll.add_argument("--default-branch", default="main")

    scanner = subparsers.add_parser("set-scanner")
    scanner.add_argument("--profile-id", required=True)
    scanner.add_argument("--scanner", required=True)
    scanner.add_argument("--enabled", choices=("true", "false"), required=True)

    issue_policy = subparsers.add_parser("set-issue-policy")
    issue_policy.add_argument("--profile-id", required=True)
    issue_policy.add_argument("--mode", choices=("zero", "ratchet", "audit"), required=True)

    coverage = subparsers.add_parser("set-coverage-mode")
    coverage.add_argument("--profile-id", required=True)
    coverage.add_argument("--event-name", choices=("default", "push", "pull_request"), required=True)
    coverage.add_argument("--mode", choices=("enforce", "evidence_only"), required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    if args.command == "enroll-repo":
        enroll_repo(
            repo_root=repo_root,
            repo_slug=args.repo_slug,
            profile_id=args.profile_id,
            stack=args.stack,
            rollout=args.rollout,
            default_branch=args.default_branch,
        )
    elif args.command == "set-scanner":
        set_scanner(repo_root=repo_root, profile_id=args.profile_id, scanner=args.scanner, enabled=args.enabled == "true")
    elif args.command == "set-issue-policy":
        set_issue_policy(repo_root=repo_root, profile_id=args.profile_id, mode=args.mode)
    else:
        event_name = "default" if args.event_name == "default" else args.event_name
        set_coverage_mode(repo_root=repo_root, profile_id=args.profile_id, event_name=event_name, mode=args.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
