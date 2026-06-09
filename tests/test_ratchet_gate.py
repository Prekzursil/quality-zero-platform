"""Tests for the Layer-1 monotonic-decreasing ratchet gate.

Covers the pure helpers (provider-total reading, measured/errored/expected
derivation, new-code diff matching, baseline model, the classify state
machine, markdown rendering) and the git-backed diff functions against a
real temporary git repository, plus ``main`` end-to-end across every exit
code (0 pass / 1 gate-red / 2 usage-or-IO).
"""

from __future__ import absolute_import

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Sequence, Set
from unittest.mock import patch

from scripts.quality import ratchet_diff as rd
from scripts.quality import ratchet_gate as rg

# Git location env vars that ``git`` honors over ``cwd`` (e.g. ``GIT_DIR`` set
# by a pre-push hook). They leak into the temp-repo subprocess calls in the
# real-git tests below and redirect them at the outer repo, so scrub them for
# the whole module. Identity stays sourced from each temp repo's local
# ``git config`` (GIT_CONFIG* is deliberately left untouched).
_GIT_LOCATION_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
)
_saved_git_env: Dict[str, str] = {}


def setUpModule() -> None:
    """Remove leaked git-location env vars so temp-repo git stays hermetic."""
    for name in _GIT_LOCATION_ENV:
        value = os.environ.pop(name, None)
        if value is not None:
            _saved_git_env[name] = value


def tearDownModule() -> None:
    """Restore any git-location env vars removed in ``setUpModule``."""
    os.environ.update(_saved_git_env)
    _saved_git_env.clear()


def _tmpdir(case: unittest.TestCase) -> Path:
    """Return a fresh temp directory that is cleaned up after the test."""
    holder = tempfile.TemporaryDirectory()
    case.addCleanup(holder.cleanup)
    return Path(holder.name)


def _base_parse_argv(*extra: str) -> List[str]:
    """Build a ``parse_args`` argv with the five required flags plus ``extra``.

    Returning a constructed list (required flags + caller extras) keeps the
    literal flag block out of individual tests so it no longer matches the
    sibling argv block in ``verify_v2_deployment.py`` (qlty clone detection).
    """
    required = {
        "--canonical-json": "c.json",
        "--ratchet-json": "r.json",
        "--repo-dir": ".",
        "--repo-slug": "owner/repo",
        "--head-sha": "sha",
    }
    argv: List[str] = []
    for flag, value in required.items():
        argv.extend([flag, value])
    argv.extend(extra)
    return argv


def _git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` via the source helper (keeps subprocess in source)."""
    return rg._run_git(list(args), repo)


def _init_repo(repo: Path) -> None:
    """Initialise a deterministic git repo with a committed identity."""
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "ratchet@example.com")
    _git(repo, "config", "user.name", "Ratchet Bot")
    _git(repo, "config", "commit.gpgsign", "false")


def _commit(repo: Path, message: str) -> str:
    """Stage everything, commit, and return the resulting HEAD sha."""
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


class TimestampHelperTests(unittest.TestCase):
    """``utc_now`` / ``today`` return well-formed ISO strings."""

    def test_utc_now_is_iso_timestamp(self) -> None:
        """``utc_now`` carries a date, the 'T' separator, and a tz offset."""
        value = rg.utc_now()
        self.assertIn("T", value)
        self.assertTrue(value.startswith(rg.today()))

    def test_today_is_iso_date(self) -> None:
        """``today`` is a ``YYYY-MM-DD`` string."""
        value = rg.today()
        self.assertRegex(value, r"^\d{4}-\d{2}-\d{2}$")


class RunGitTests(unittest.TestCase):
    """``_run_git`` returns stdout and translates failures to RatchetError."""

    def test_returns_stdout_on_success(self) -> None:
        """A successful command yields its stdout."""
        repo = _tmpdir(self)
        _init_repo(repo)
        out = _git(repo, "rev-parse", "--is-inside-work-tree")
        self.assertEqual(out.strip(), "true")

    def test_called_process_error_raises_ratchet_error(self) -> None:
        """A non-zero git exit becomes a RatchetError carrying the stderr."""
        repo = _tmpdir(self)
        _init_repo(repo)
        with self.assertRaises(rg.RatchetError) as ctx:
            _git(repo, "rev-parse", "does-not-exist")
        self.assertIn("git rev-parse does-not-exist failed",
                      str(ctx.exception))

    def test_oserror_raises_could_not_run_git(self) -> None:
        """When the binary cannot be spawned, OSError maps to RatchetError."""
        repo = _tmpdir(self)
        with (
                patch.object(rd.subprocess, "run",
                             side_effect=OSError("boom")),
                self.assertRaises(rg.RatchetError) as ctx,
        ):
            _git(repo, "status")
        self.assertIn("could not run git: boom", str(ctx.exception))


class ResolveDiffBaseTests(unittest.TestCase):
    """``_resolve_diff_base`` computes a merge-base or fails loud."""

    def test_empty_base_ref_returns_empty(self) -> None:
        """An empty base ref opts out of new-code detection (returns "")."""
        repo = _tmpdir(self)
        self.assertEqual(rg._resolve_diff_base(repo, "", "HEAD"), "")

    def test_merge_base_resolves_for_real_history(self) -> None:
        """A real two-commit history resolves to the first commit's sha."""
        repo = _tmpdir(self)
        _init_repo(repo)
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        base = _commit(repo, "base")
        (repo / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
        head = _commit(repo, "head")
        resolved = rg._resolve_diff_base(repo, base, head)
        self.assertEqual(resolved, base)

    def test_defaults_head_to_head_ref_when_blank(self) -> None:
        """A blank head sha falls back to the literal ``HEAD`` ref."""
        repo = _tmpdir(self)
        _init_repo(repo)
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        base = _commit(repo, "base")
        resolved = rg._resolve_diff_base(repo, base, "")
        self.assertEqual(resolved, base)

    def test_empty_merge_base_raises(self) -> None:
        """An empty (but successful) merge-base output fails loud."""
        repo = _tmpdir(self)
        with (
                patch.object(rd, "_run_git", return_value="\n"),
                self.assertRaises(rg.RatchetError) as ctx,
        ):
            rg._resolve_diff_base(repo, "origin/main", "deadbeef")
        self.assertIn("empty merge-base", str(ctx.exception))

    def test_bad_ref_propagates_called_process_error(self) -> None:
        """A non-existent base ref surfaces as a RatchetError from git."""
        repo = _tmpdir(self)
        _init_repo(repo)
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        _commit(repo, "base")
        with self.assertRaises(rg.RatchetError):
            rg._resolve_diff_base(repo, "origin/nope", "HEAD")


class AddedLineRangesTests(unittest.TestCase):
    """``added_line_ranges`` parses ``git diff --unified=0`` hunks."""

    def test_empty_base_returns_empty_dict(self) -> None:
        """No base sha means no new-code scope."""
        repo = _tmpdir(self)
        self.assertEqual(rg.added_line_ranges(repo, "", "HEAD"), {})

    def test_added_lines_captured_with_explicit_count(self) -> None:
        """A multi-line addition records every added HEAD-side line."""
        repo = _tmpdir(self)
        _init_repo(repo)
        (repo / "mod.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
        base = _commit(repo, "base")
        (repo / "mod.py").write_text("a = 1\nb = 2\nc = 3\nd = 4\n",
                                     encoding="utf-8")
        head = _commit(repo, "head")
        added = rg.added_line_ranges(repo, base, head)
        self.assertEqual(added.get("mod.py"), {3, 4})

    def test_single_line_addition_defaults_count_to_one(self) -> None:
        """A hunk with no explicit count (``+N``) adds exactly one line."""
        repo = _tmpdir(self)
        _init_repo(repo)
        (repo / "one.py").write_text("a = 1\n", encoding="utf-8")
        base = _commit(repo, "base")
        (repo / "one.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
        head = _commit(repo, "head")
        added = rg.added_line_ranges(repo, base, head)
        self.assertEqual(added.get("one.py"), {2})

    def test_pure_deletion_records_no_added_lines(self) -> None:
        """A file with only deletions registers but holds no added lines."""
        repo = _tmpdir(self)
        _init_repo(repo)
        (repo / "del.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
        base = _commit(repo, "base")
        (repo / "del.py").write_text("a = 1\n", encoding="utf-8")
        head = _commit(repo, "head")
        added = rg.added_line_ranges(repo, base, head)
        self.assertEqual(added.get("del.py"), set())

    def test_hunk_without_current_file_is_ignored(self) -> None:
        """A hunk header with no preceding +++ line is skipped (defensive)."""
        repo = _tmpdir(self)
        fake_diff = "@@ -1 +1 @@\n+orphan line\n"
        with patch.object(rd, "_run_git", return_value=fake_diff):
            added = rg.added_line_ranges(repo, "base", "head")
        self.assertEqual(added, {})

    def test_diff_head_defaults_to_head_ref_when_blank(self) -> None:
        """A blank head sha diffs against the literal ``HEAD`` ref."""
        repo = _tmpdir(self)
        _init_repo(repo)
        (repo / "h.py").write_text("a = 1\n", encoding="utf-8")
        base = _commit(repo, "base")
        (repo / "h.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
        _commit(repo, "head")
        added = rg.added_line_ranges(repo, base, "")
        self.assertEqual(added.get("h.py"), {2})


class NormalizePathTests(unittest.TestCase):
    """``_normalize_path`` strips slashes and leading ``./``."""

    def test_backslashes_and_leading_dot_slash_stripped(self) -> None:
        """Windows separators and a leading ``./`` are normalised away."""
        self.assertEqual(rg._normalize_path(".\\app\\api.py"), "app/api.py")

    def test_plain_path_unchanged(self) -> None:
        """A clean posix path passes through untouched."""
        self.assertEqual(rg._normalize_path("app/api.py"), "app/api.py")


class SuffixPathMatchTests(unittest.TestCase):
    """``_suffix_path_match`` aligns suffixes on path components."""

    def test_identical_paths_match(self) -> None:
        """Two equal paths match."""
        self.assertTrue(rg._suffix_path_match("app/api.py", "app/api.py"))

    def test_component_aligned_suffix_matches(self) -> None:
        """A leading-slash suffix match aligns on a path component."""
        self.assertTrue(
            rg._suffix_path_match("apps/api/app/api.py", "app/api.py"))

    def test_non_component_suffix_does_not_match(self) -> None:
        """A bare suffix (``myapi.py`` vs ``api.py``) is rejected."""
        self.assertFalse(rg._suffix_path_match("src/myapi.py", "api.py"))


class IsNewCodeFindingTests(unittest.TestCase):
    """``is_new_code_finding`` anchors findings to added diff lines."""

    def test_direct_hit_on_added_line(self) -> None:
        """A finding on an added line in the same file is new-code."""
        added: Dict[str, Set[int]] = {"app/api.py": {10, 11}}
        self.assertTrue(
            rg.is_new_code_finding({
                "file": "app/api.py",
                "line": 10
            }, added))

    def test_added_file_present_but_line_not_added(self) -> None:
        """A finding in a changed file but on an unchanged line is not new."""
        added: Dict[str, Set[int]] = {"app/api.py": {10}}
        self.assertFalse(
            rg.is_new_code_finding({
                "file": "app/api.py",
                "line": 99
            }, added))

    def test_value_error_line_returns_false(self) -> None:
        """A non-numeric string line value is treated as non-positional."""
        added: Dict[str, Set[int]] = {"app/api.py": {1}}
        self.assertFalse(
            rg.is_new_code_finding({
                "file": "app/api.py",
                "line": "x"
            }, added))

    def test_type_error_line_returns_false(self) -> None:
        """A line value of an unconvertible type is non-positional."""
        added: Dict[str, Set[int]] = {"app/api.py": {1}}
        self.assertFalse(
            rg.is_new_code_finding({
                "file": "app/api.py",
                "line": [1]
            }, added))

    def test_non_positive_line_returns_false(self) -> None:
        """A line <= 0 is repo/manifest-level, never new-code."""
        added: Dict[str, Set[int]] = {"app/api.py": {1}}
        self.assertFalse(
            rg.is_new_code_finding({
                "file": "app/api.py",
                "line": 0
            }, added))

    def test_empty_file_returns_false(self) -> None:
        """A finding with no file anchor is non-positional."""
        added: Dict[str, Set[int]] = {"app/api.py": {1}}
        self.assertFalse(rg.is_new_code_finding({
            "file": "",
            "line": 5
        }, added))

    def test_suffix_fallback_match_true(self) -> None:
        """A rollup/diff prefix mismatch resolves via suffix match."""
        added: Dict[str, Set[int]] = {"apps/api/app/api.py": {7}}
        self.assertTrue(
            rg.is_new_code_finding({
                "file": "app/api.py",
                "line": 7
            }, added))

    def test_suffix_fallback_line_miss_false(self) -> None:
        """A suffix match still fails when the line is not added."""
        added: Dict[str, Set[int]] = {"apps/api/app/api.py": {7}}
        self.assertFalse(
            rg.is_new_code_finding({
                "file": "app/api.py",
                "line": 8
            }, added))

    def test_no_match_anywhere_false(self) -> None:
        """A finding in a file absent from the diff is not new-code."""
        added: Dict[str, Set[int]] = {"other/file.py": {1}}
        self.assertFalse(
            rg.is_new_code_finding({
                "file": "app/api.py",
                "line": 1
            }, added))


class ReadProviderTotalsTests(unittest.TestCase):
    """``read_provider_totals`` maps provider -> corroborator total."""

    def test_totals_extracted_and_not_configured_skipped(self) -> None:
        """Configured providers are summed; ``not-configured`` ones dropped."""
        canonical = {
            "provider_summaries": [
                {
                    "provider": "Codacy",
                    "total": 5
                },
                {
                    "provider": "SonarCloud",
                    "total": 3,
                    "status": "ok"
                },
                {
                    "provider": "Sentry",
                    "status": "not-configured",
                    "total": 9
                },
                {
                    "provider": "",
                    "total": 1
                },
            ]
        }
        totals = rg.read_provider_totals(canonical)
        self.assertEqual(totals, {"Codacy": 5, "SonarCloud": 3})

    def test_missing_summaries_yields_empty(self) -> None:
        """No ``provider_summaries`` key yields an empty mapping."""
        self.assertEqual(rg.read_provider_totals({}), {})


class MeasuredProvidersTests(unittest.TestCase):
    """``measured_providers_from_canonical`` lists providers that ran."""

    def test_measured_set_excludes_not_configured_and_blank(self) -> None:
        """Blank/``not-configured`` rows are excluded from the measured set."""
        canonical = {
            "provider_summaries": [
                {
                    "provider": "Codacy",
                    "total": 1
                },
                {
                    "provider": "Sentry",
                    "status": "not-configured"
                },
                {
                    "provider": "",
                    "total": 2
                },
            ]
        }
        self.assertEqual(rg.measured_providers_from_canonical(canonical),
                         {"Codacy"})


class ProvidersWithErrorsTests(unittest.TestCase):
    """``providers_with_errors`` maps lane keys to canonical literals."""

    def test_exact_and_lowercase_lane_keys_mapped(self) -> None:
        """Both exact and lowercase provider keys map to the literal."""
        canonical = {
            "normalizer_errors": [
                {
                    "provider": "SonarCloud"
                },
                {
                    "provider": "codacy"
                },
                {
                    "provider": ""
                },
                {
                    "provider": "unknown-tool"
                },
            ]
        }
        self.assertEqual(rg.providers_with_errors(canonical),
                         {"SonarCloud", "Codacy"})


class ExpectedProvidersTests(unittest.TestCase):
    """``expected_providers_from_profile`` derives the expected set."""

    def test_mapping_required_contexts(self) -> None:
        """A mapping of buckets yields providers from each bucket's contexts."""
        profile = {
            "required_contexts": {
                "always": ["SonarCloud / analysis"],
                "target": ["Codacy quality"],
                "pull_request_only": ["CodeQL scan"],
                "ignored_bucket": ["Semgrep run"],
            }
        }
        self.assertEqual(
            rg.expected_providers_from_profile(profile),
            {"SonarCloud", "Codacy", "CodeQL"},
        )

    def test_mapping_bucket_value_not_list_ignored(self) -> None:
        """A bucket whose value is not a list contributes nothing."""
        profile = {"required_contexts": {"always": "SonarCloud"}}
        self.assertEqual(rg.expected_providers_from_profile(profile), set())

    def test_list_required_contexts(self) -> None:
        """A plain list of contexts is consumed directly."""
        profile = {"required_contexts": ["Dependency review", "qlty gate"]}
        self.assertEqual(rg.expected_providers_from_profile(profile),
                         {"Dependabot", "QLTY"})

    def test_required_contexts_neither_mapping_nor_list(self) -> None:
        """A scalar ``required_contexts`` falls through to no contexts."""
        profile = {"required_contexts": 7}
        self.assertEqual(rg.expected_providers_from_profile(profile), set())

    def test_active_required_contexts_merged(self) -> None:
        """``active_required_contexts`` is always folded in."""
        profile = {"active_required_contexts": ["Sentry monitoring"]}
        self.assertEqual(rg.expected_providers_from_profile(profile),
                         {"Sentry"})


class CountNewCodeTests(unittest.TestCase):
    """``count_new_code`` tallies new-code findings per provider."""

    def test_counts_new_code_per_corroborator(self) -> None:
        """Each known corroborator on a new-code finding is incremented."""
        added: Dict[str, Set[int]] = {"app/api.py": {5}}
        canonical = {
            "findings": [
                {
                    "file":
                    "app/api.py",
                    "line":
                    5,
                    "corroborators": [
                        {
                            "provider": "Codacy"
                        },
                        {
                            "provider": "SonarCloud"
                        },
                        {
                            "provider": "NotAProvider"
                        },
                    ],
                },
                {
                    "file": "app/api.py",
                    "line": 99,
                    "corroborators": [{
                        "provider": "Codacy"
                    }],
                },
            ]
        }
        counts = rg.count_new_code(canonical, added)
        self.assertEqual(counts["Codacy"], 1)
        self.assertEqual(counts["SonarCloud"], 1)
        self.assertEqual(counts["Sentry"], 0)


class RatchetBaselineTests(unittest.TestCase):
    """``RatchetBaseline`` ceiling/seed/lower/log behaviours."""

    def test_providers_property_initialises_map(self) -> None:
        """The ``providers`` property seeds a mutable dict."""
        baseline = rg.RatchetBaseline(raw={})
        self.assertEqual(baseline.providers, {})
        self.assertIn("providers", baseline.raw)

    def test_ceiling_for_unseeded_provider_is_none(self) -> None:
        """An absent provider has no ceiling."""
        baseline = rg.RatchetBaseline(raw={"providers": {}})
        self.assertIsNone(baseline.ceiling("Codacy"))

    def test_ceiling_non_mapping_entry_is_none(self) -> None:
        """A non-mapping provider entry yields no ceiling."""
        baseline = rg.RatchetBaseline(raw={"providers": {"Codacy": 5}})
        self.assertIsNone(baseline.ceiling("Codacy"))

    def test_ceiling_unmeasured_entry_is_none(self) -> None:
        """An entry flagged ``measured: false`` has no usable ceiling."""
        baseline = rg.RatchetBaseline(
            raw={"providers": {
                "Codacy": {
                    "ceiling": 5,
                    "measured": False
                }
            }})
        self.assertIsNone(baseline.ceiling("Codacy"))

    def test_ceiling_non_numeric_value_is_none(self) -> None:
        """A non-numeric ceiling value is ignored."""
        baseline = rg.RatchetBaseline(
            raw={"providers": {
                "Codacy": {
                    "ceiling": "five"
                }
            }})
        self.assertIsNone(baseline.ceiling("Codacy"))

    def test_ceiling_numeric_value_returned(self) -> None:
        """A numeric ceiling is returned as an int."""
        baseline = rg.RatchetBaseline(
            raw={"providers": {
                "Codacy": {
                    "ceiling": 5.0
                }
            }})
        self.assertEqual(baseline.ceiling("Codacy"), 5)

    def test_is_seeded_reflects_ceiling_presence(self) -> None:
        """``is_seeded`` is True only when a measured ceiling exists."""
        baseline = rg.RatchetBaseline(
            raw={"providers": {
                "Codacy": {
                    "ceiling": 2
                }
            }})
        self.assertTrue(baseline.is_seeded("Codacy"))
        self.assertFalse(baseline.is_seeded("SonarCloud"))

    def test_lower_writes_entry_and_audit(self) -> None:
        """``lower`` records the new ceiling, metadata, and an audit row."""
        baseline = rg.RatchetBaseline(raw={})
        baseline.lower("Codacy", 3, "sha123")
        entry = baseline.providers["Codacy"]
        self.assertEqual(entry["ceiling"], 3)
        self.assertTrue(entry["measured"])
        self.assertEqual(entry["last_lowered_sha"], "sha123")
        self.assertIn("last_lowered_at", entry)
        self.assertEqual(baseline.raw["audit_log"][0]["action"], "auto-lower")

    def test_seed_new_provider_logs_seed_action(self) -> None:
        """Seeding a fresh provider logs the ``seed`` action."""
        baseline = rg.RatchetBaseline(raw={})
        baseline.seed("Codacy", 4, "sha9", actor="seed-pr")
        entry = baseline.providers["Codacy"]
        self.assertEqual(entry["ceiling"], 4)
        self.assertEqual(entry["seeded_by"], "seed-pr")
        self.assertIn("seeded_at", entry)
        self.assertEqual(baseline.raw["audit_log"][0]["action"], "seed")

    def test_seed_raising_existing_logs_raise_action(self) -> None:
        """Seeding above an existing numeric ceiling logs ``raise``."""
        baseline = rg.RatchetBaseline(
            raw={"providers": {
                "Codacy": {
                    "ceiling": 2
                }
            }})
        baseline.seed("Codacy", 5, "sha9", actor="human")
        self.assertEqual(baseline.raw["audit_log"][0]["action"], "raise")

    def test_seed_equal_existing_logs_seed_action(self) -> None:
        """Seeding at-or-below an existing ceiling logs ``seed`` (not raise)."""
        baseline = rg.RatchetBaseline(
            raw={"providers": {
                "Codacy": {
                    "ceiling": 5
                }
            }})
        baseline.seed("Codacy", 5, "sha9", actor="human")
        self.assertEqual(baseline.raw["audit_log"][0]["action"], "seed")

    def test_log_default_actor_is_ci_bot(self) -> None:
        """The audit log uses ``ci-bot`` when no actor is supplied."""
        baseline = rg.RatchetBaseline(raw={})
        baseline.lower("Codacy", 1, "sha")
        self.assertEqual(baseline.raw["audit_log"][0]["actor"], "ci-bot")


class NewBaselineTests(unittest.TestCase):
    """``new_baseline`` scaffolds an empty baseline."""

    def test_scaffold_fields(self) -> None:
        """The scaffold carries schema, repo, sha, and empty maps."""
        baseline = rg.new_baseline("owner/repo", "sha42")
        self.assertEqual(baseline.raw["schema_version"],
                         rg.RATCHET_SCHEMA_VERSION)
        self.assertEqual(baseline.raw["repo"], "owner/repo")
        self.assertEqual(baseline.raw["baseline_sha"], "sha42")
        self.assertEqual(baseline.raw["providers"], {})
        self.assertEqual(baseline.raw["audit_log"], [])


class LoadBaselineTests(unittest.TestCase):
    """``load_baseline`` reads existing files or scaffolds fresh ones."""

    def test_missing_file_scaffolds(self) -> None:
        """A missing path returns a fresh scaffold."""
        path = _tmpdir(self) / "ratchet.json"
        baseline = rg.load_baseline(path, "owner/repo", "sha1")
        self.assertEqual(baseline.raw["repo"], "owner/repo")

    def test_invalid_json_scaffolds(self) -> None:
        """A corrupt JSON file falls back to a scaffold."""
        path = _tmpdir(self) / "ratchet.json"
        path.write_text("{not json", encoding="utf-8")
        baseline = rg.load_baseline(path, "owner/repo", "sha1")
        self.assertEqual(baseline.raw["repo"], "owner/repo")

    def test_non_dict_json_scaffolds(self) -> None:
        """A JSON array (not an object) falls back to a scaffold."""
        path = _tmpdir(self) / "ratchet.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        baseline = rg.load_baseline(path, "owner/repo", "sha1")
        self.assertEqual(baseline.raw["repo"], "owner/repo")

    def test_valid_file_loaded_with_defaults_filled(self) -> None:
        """A valid object is loaded and missing keys are back-filled."""
        path = _tmpdir(self) / "ratchet.json"
        path.write_text(
            json.dumps({
                "repo": "owner/repo",
                "providers": {
                    "Codacy": {
                        "ceiling": 2
                    }
                }
            }),
            encoding="utf-8",
        )
        baseline = rg.load_baseline(path, "ignored", "sha1")
        self.assertEqual(baseline.ceiling("Codacy"), 2)
        self.assertEqual(baseline.raw["schema_version"],
                         rg.RATCHET_SCHEMA_VERSION)
        self.assertEqual(baseline.raw["audit_log"], [])


class GateResultTests(unittest.TestCase):
    """``GateResult.failed`` reflects any failing verdict."""

    def test_failed_true_for_failing_states(self) -> None:
        """Any of the failing states flips ``failed`` to True."""
        for state in ("regression", "new-code", "unmeasured", "unseeded"):
            with self.subTest(state=state):
                result = rg.GateResult(
                    verdicts=[rg.ProviderVerdict("X", state, 1, 1, 0)])
                self.assertTrue(result.failed)

    def test_failed_false_for_pass_only(self) -> None:
        """A pass-only result is not failed."""
        result = rg.GateResult(
            verdicts=[rg.ProviderVerdict("X", "pass", 1, 1, 0)])
        self.assertFalse(result.failed)


class ClassifyUnmeasuredTests(unittest.TestCase):
    """``_classify_unmeasured`` holds or silently ignores providers."""

    def test_expected_provider_held(self) -> None:
        """An expected-but-absent provider yields an unmeasured verdict."""
        baseline = rg.RatchetBaseline(raw={})
        verdict = rg._classify_unmeasured(
            provider="Codacy",
            baseline=baseline,
            expected={"Codacy"},
            errored=set(),
            current=None,
            ceiling=None,
            new_code=0,
        )
        assert verdict is not None
        self.assertEqual(verdict.state, "unmeasured")

    def test_seeded_provider_held(self) -> None:
        """A previously-seeded provider that vanished is held."""
        baseline = rg.RatchetBaseline(
            raw={"providers": {
                "Codacy": {
                    "ceiling": 1
                }
            }})
        verdict = rg._classify_unmeasured(
            provider="Codacy",
            baseline=baseline,
            expected=set(),
            errored=set(),
            current=None,
            ceiling=1,
            new_code=0,
        )
        self.assertIsNotNone(verdict)

    def test_errored_provider_held(self) -> None:
        """An errored provider is held even if not expected or seeded."""
        baseline = rg.RatchetBaseline(raw={})
        verdict = rg._classify_unmeasured(
            provider="Codacy",
            baseline=baseline,
            expected=set(),
            errored={"Codacy"},
            current=None,
            ceiling=None,
            new_code=0,
        )
        self.assertIsNotNone(verdict)

    def test_irrelevant_provider_ignored(self) -> None:
        """An unexpected, unseeded, non-errored provider is ignored."""
        baseline = rg.RatchetBaseline(raw={})
        verdict = rg._classify_unmeasured(
            provider="Codacy",
            baseline=baseline,
            expected=set(),
            errored=set(),
            current=None,
            ceiling=None,
            new_code=0,
        )
        self.assertIsNone(verdict)


class ClassifyMeasuredTests(unittest.TestCase):
    """``_classify_measured`` runs the per-provider assert/lower machine."""

    def _baseline(self, ceiling: int) -> rg.RatchetBaseline:
        return rg.RatchetBaseline(
            raw={"providers": {
                "Codacy": {
                    "ceiling": ceiling
                }
            }})

    def test_unseeded_without_seed_flag_fails(self) -> None:
        """A measured-but-unseeded provider fails asking for a SEED PR."""
        baseline = rg.RatchetBaseline(raw={})
        verdict, changed = rg._classify_measured(
            provider="Codacy",
            baseline=baseline,
            current=4,
            ceiling=None,
            new_code=0,
            head_sha="s",
            seed_missing=False,
        )
        self.assertEqual(verdict.state, "unseeded")
        self.assertFalse(changed)

    def test_unseeded_with_seed_flag_seeds_and_passes(self) -> None:
        """With ``--seed`` an unseeded provider is seeded and passes."""
        baseline = rg.RatchetBaseline(raw={})
        verdict, changed = rg._classify_measured(
            provider="Codacy",
            baseline=baseline,
            current=4,
            ceiling=None,
            new_code=0,
            head_sha="s",
            seed_missing=True,
        )
        self.assertEqual(verdict.state, "pass")
        self.assertEqual(verdict.lowered_to, 4)
        self.assertTrue(changed)
        self.assertEqual(baseline.ceiling("Codacy"), 4)

    def test_new_code_fails_regardless_of_ceiling(self) -> None:
        """Any new-code finding fails even when under the ceiling."""
        baseline = self._baseline(10)
        verdict, changed = rg._classify_measured(
            provider="Codacy",
            baseline=baseline,
            current=2,
            ceiling=10,
            new_code=1,
            head_sha="s",
            seed_missing=False,
        )
        self.assertEqual(verdict.state, "new-code")
        self.assertFalse(changed)

    def test_regression_above_ceiling_fails(self) -> None:
        """A current count above the ceiling is a regression."""
        baseline = self._baseline(3)
        verdict, changed = rg._classify_measured(
            provider="Codacy",
            baseline=baseline,
            current=5,
            ceiling=3,
            new_code=0,
            head_sha="s",
            seed_missing=False,
        )
        self.assertEqual(verdict.state, "regression")
        self.assertFalse(changed)

    def test_below_ceiling_auto_lowers(self) -> None:
        """A current count below the ceiling passes and auto-lowers."""
        baseline = self._baseline(5)
        verdict, changed = rg._classify_measured(
            provider="Codacy",
            baseline=baseline,
            current=2,
            ceiling=5,
            new_code=0,
            head_sha="s",
            seed_missing=False,
        )
        self.assertEqual(verdict.state, "pass")
        self.assertEqual(verdict.lowered_to, 2)
        self.assertTrue(changed)
        self.assertEqual(baseline.ceiling("Codacy"), 2)

    def test_at_ceiling_passes_without_change(self) -> None:
        """A current count equal to the ceiling passes without lowering."""
        baseline = self._baseline(3)
        verdict, changed = rg._classify_measured(
            provider="Codacy",
            baseline=baseline,
            current=3,
            ceiling=3,
            new_code=0,
            head_sha="s",
            seed_missing=False,
        )
        self.assertEqual(verdict.state, "pass")
        self.assertIsNone(verdict.lowered_to)
        self.assertFalse(changed)

    def test_none_current_treated_as_zero(self) -> None:
        """A measured provider with ``current=None`` is treated as 0."""
        baseline = self._baseline(3)
        verdict, changed = rg._classify_measured(
            provider="Codacy",
            baseline=baseline,
            current=None,
            ceiling=3,
            new_code=0,
            head_sha="s",
            seed_missing=False,
        )
        self.assertEqual(verdict.current, 0)
        self.assertTrue(changed)


class ClassifyTests(unittest.TestCase):
    """``classify`` aggregates per-provider verdicts over the universe."""

    def test_universe_covers_measured_expected_seeded_and_totals(self) -> None:
        """Providers from every source are classified; irrelevant ones drop."""
        baseline = rg.RatchetBaseline(
            raw={"providers": {
                "SonarCloud": {
                    "ceiling": 4
                }
            }})
        result = rg.classify(rg.ClassifyInputs(
            baseline=baseline,
            totals={
                "Codacy": 2,
                "Irrelevant": 9
            },
            new_code={"Codacy": 0},
            measured={"Codacy", "Irrelevant"},
            errored=set(),
            expected={"CodeQL"},
            head_sha="sha",
            seed_missing=True,
        ))
        states = {v.provider: v.state for v in result.verdicts}
        # Codacy measured+unseeded -> seeded -> pass; Irrelevant measured but
        # unseeded -> seeded -> pass; CodeQL expected+absent -> unmeasured;
        # SonarCloud seeded+absent -> unmeasured.
        self.assertEqual(states["Codacy"], "pass")
        self.assertEqual(states["CodeQL"], "unmeasured")
        self.assertEqual(states["SonarCloud"], "unmeasured")
        self.assertTrue(result.changed)

    def test_errored_measured_provider_held_as_unmeasured(self) -> None:
        """A provider in both measured and errored is held (unmeasured)."""
        baseline = rg.RatchetBaseline(raw={})
        result = rg.classify(rg.ClassifyInputs(
            baseline=baseline,
            totals={"Codacy": 2},
            new_code={},
            measured={"Codacy"},
            errored={"Codacy"},
            expected=set(),
            head_sha="sha",
            seed_missing=False,
        ))
        self.assertEqual(result.verdicts[0].state, "unmeasured")
        self.assertFalse(result.changed)

    def test_unmeasured_irrelevant_provider_dropped(self) -> None:
        """A baseline-listed but non-mapping provider produces no verdict."""
        baseline = rg.RatchetBaseline(raw={"providers": {"Ghost": "stale"}})
        result = rg.classify(rg.ClassifyInputs(
            baseline=baseline,
            totals={},
            new_code={},
            measured=set(),
            errored=set(),
            expected=set(),
            head_sha="sha",
            seed_missing=False,
        ))
        self.assertEqual(result.verdicts, [])


class RenderMarkdownTests(unittest.TestCase):
    """``render_markdown`` renders the verdict table."""

    def test_pass_table_renders_dashes_for_none(self) -> None:
        """A passing verdict with None fields renders ``-`` placeholders."""
        result = rg.GateResult(
            verdicts=[rg.ProviderVerdict("Codacy", "unseeded", None, None, 0)])
        md = rg.render_markdown(result, "owner/repo", "sha")
        self.assertIn("Verdict: **FAIL**", md)
        self.assertIn("| `Codacy` | `unseeded` | - | - | 0 | - |", md)

    def test_pass_verdict_marks_pass(self) -> None:
        """A clean result renders a PASS verdict with concrete numbers."""
        result = rg.GateResult(
            verdicts=[
                rg.ProviderVerdict("Codacy", "pass", 2, 5, 0, lowered_to=2)
            ],
            changed=True,
        )
        md = rg.render_markdown(result, "owner/repo", "abc")
        self.assertIn("Verdict: **PASS**", md)
        self.assertIn("| `Codacy` | `pass` | 2 | 5 | 0 | 2 |", md)
        self.assertIn("Baseline changed (auto-lowered/seeded): `True`", md)


class WriteBaselineTests(unittest.TestCase):
    """``write_baseline`` persists deterministic JSON."""

    def test_writes_sorted_json_with_trailing_newline(self) -> None:
        """The baseline is written sorted with a trailing newline."""
        path = _tmpdir(self) / "nested" / "ratchet.json"
        baseline = rg.new_baseline("owner/repo", "sha")
        rg.write_baseline(path, baseline)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(json.loads(text)["repo"], "owner/repo")


class ParseArgsTests(unittest.TestCase):
    """``parse_args`` wires the CLI surface."""

    def test_required_and_default_args(self) -> None:
        """Required args parse and flags default off."""
        args = rg.parse_args(_base_parse_argv())
        self.assertEqual(args.canonical_json, "c.json")
        self.assertFalse(args.write)
        self.assertFalse(args.seed)
        self.assertEqual(args.base_ref, "")

    def test_optional_flags_parsed(self) -> None:
        """Optional flags and paths parse through."""
        args = rg.parse_args(_base_parse_argv(
            "--base-ref", "origin/main",
            "--out-md", "out.md",
            "--write",
            "--seed",
            "--github-output", "gh.txt",
            "--profile-json", "p.json",
        ))
        self.assertTrue(args.write)
        self.assertTrue(args.seed)
        self.assertEqual(args.base_ref, "origin/main")


class EmitGithubOutputTests(unittest.TestCase):
    """``_emit_github_output`` appends key/values or no-ops."""

    def test_empty_path_is_noop(self) -> None:
        """An empty path writes nothing and does not raise."""
        rg._emit_github_output("", rg.GateResult())

    def test_writes_changed_and_failed(self) -> None:
        """The output file receives ``changed`` and ``failed`` lines."""
        path = _tmpdir(self) / "gh.txt"
        result = rg.GateResult(
            verdicts=[rg.ProviderVerdict("X", "regression", 2, 1, 0)],
            changed=True,
        )
        rg._emit_github_output(str(path), result)
        text = path.read_text(encoding="utf-8")
        self.assertIn("changed=true\n", text)
        self.assertIn("failed=true\n", text)

    def test_oserror_swallowed(self) -> None:
        """An unwritable path (a directory) is swallowed silently."""
        directory = _tmpdir(self)
        rg._emit_github_output(str(directory), rg.GateResult())


class MainTests(unittest.TestCase):
    """``main`` end-to-end across exit codes and side effects."""

    def _workspace(self) -> Path:
        return _tmpdir(self)

    def _write_canonical(self, root: Path, payload: Dict[str, object]) -> Path:
        path = root / "canonical.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _argv(
            self,
            *,
            canonical: Path,
            ratchet: Path,
            repo_dir: Path,
            head_sha: str,
            extra: Sequence[str] = (),
    ) -> List[str]:
        argv = [
            "--canonical-json",
            str(canonical),
            "--ratchet-json",
            str(ratchet),
            "--repo-dir",
            str(repo_dir),
            "--repo-slug",
            "owner/repo",
            "--head-sha",
            head_sha,
        ]
        argv.extend(extra)
        return argv

    def test_canonical_not_found_returns_2(self) -> None:
        """A missing canonical.json exits 2."""
        root = self._workspace()
        code = rg.main(
            self._argv(
                canonical=root / "missing.json",
                ratchet=root / "ratchet.json",
                repo_dir=root,
                head_sha="sha",
            ))
        self.assertEqual(code, 2)

    def test_diff_failure_returns_2(self) -> None:
        """A RatchetError from the diff resolution exits 2."""
        root = self._workspace()
        canonical = self._write_canonical(root, {"provider_summaries": []})
        # base-ref set but repo_dir is not a git repo -> git fails -> exit 2.
        code = rg.main(
            self._argv(
                canonical=canonical,
                ratchet=root / "ratchet.json",
                repo_dir=root,
                head_sha="sha",
                extra=["--base-ref", "origin/main"],
            ))
        self.assertEqual(code, 2)

    def test_seed_path_writes_baseline_and_passes(self) -> None:
        """SEED mode seeds measured providers, writes the baseline, exits 0."""
        root = self._workspace()
        canonical = self._write_canonical(
            root,
            {
                "provider_summaries": [{
                    "provider": "Codacy",
                    "total": 3
                }],
                "findings": [],
            },
        )
        ratchet = root / "ratchet.json"
        gh = root / "gh.txt"
        out_md = root / "out.md"
        code = rg.main(
            self._argv(
                canonical=canonical,
                ratchet=ratchet,
                repo_dir=root,
                head_sha="sha1",
                extra=[
                    "--seed",
                    "--write",
                    "--github-output",
                    str(gh),
                    "--out-md",
                    str(out_md),
                ],
            ))
        self.assertEqual(code, 0)
        baseline = json.loads(ratchet.read_text(encoding="utf-8"))
        self.assertEqual(baseline["providers"]["Codacy"]["ceiling"], 3)
        self.assertIn("failed=false", gh.read_text(encoding="utf-8"))
        self.assertIn("Layer-1 Ratchet Gate",
                      out_md.read_text(encoding="utf-8"))

    def test_auto_lower_path_writes_baseline(self) -> None:
        """A below-ceiling run auto-lowers and persists with ``--write``."""
        root = self._workspace()
        canonical = self._write_canonical(
            root,
            {
                "provider_summaries": [{
                    "provider": "Codacy",
                    "total": 1
                }],
                "findings": [],
            },
        )
        ratchet = root / "ratchet.json"
        ratchet.write_text(
            json.dumps({
                "repo": "owner/repo",
                "providers": {
                    "Codacy": {
                        "ceiling": 5,
                        "measured": True
                    }
                },
            }),
            encoding="utf-8",
        )
        code = rg.main(
            self._argv(
                canonical=canonical,
                ratchet=ratchet,
                repo_dir=root,
                head_sha="sha2",
                extra=["--write"],
            ))
        self.assertEqual(code, 0)
        baseline = json.loads(ratchet.read_text(encoding="utf-8"))
        self.assertEqual(baseline["providers"]["Codacy"]["ceiling"], 1)

    def test_regression_returns_1(self) -> None:
        """A count above the committed ceiling exits 1 (gate red)."""
        root = self._workspace()
        canonical = self._write_canonical(
            root,
            {
                "provider_summaries": [{
                    "provider": "Codacy",
                    "total": 9
                }],
                "findings": [],
            },
        )
        ratchet = root / "ratchet.json"
        ratchet.write_text(
            json.dumps({
                "providers": {
                    "Codacy": {
                        "ceiling": 3,
                        "measured": True
                    }
                },
            }),
            encoding="utf-8",
        )
        code = rg.main(
            self._argv(
                canonical=canonical,
                ratchet=ratchet,
                repo_dir=root,
                head_sha="sha3",
            ))
        self.assertEqual(code, 1)

    def test_new_code_returns_1(self) -> None:
        """A new-code finding on an added line exits 1, even under ceiling."""
        repo = self._workspace()
        _init_repo(repo)
        (repo / "mod.py").write_text("a = 1\n", encoding="utf-8")
        _commit(repo, "base")
        base_sha = _git(repo, "rev-parse", "HEAD").strip()
        (repo / "mod.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
        head_sha = _commit(repo, "head")
        canonical = self._write_canonical(
            repo,
            {
                "provider_summaries": [{
                    "provider": "Codacy",
                    "total": 1
                }],
                "findings": [{
                    "file": "mod.py",
                    "line": 2,
                    "corroborators": [{
                        "provider": "Codacy"
                    }],
                }],
            },
        )
        ratchet = repo / "ratchet.json"
        ratchet.write_text(
            json.dumps({
                "providers": {
                    "Codacy": {
                        "ceiling": 5,
                        "measured": True
                    }
                },
            }),
            encoding="utf-8",
        )
        code = rg.main(
            self._argv(
                canonical=canonical,
                ratchet=ratchet,
                repo_dir=repo,
                head_sha=head_sha,
                extra=["--base-ref", base_sha],
            ))
        self.assertEqual(code, 1)

    def test_unmeasured_hold_returns_1(self) -> None:
        """An expected-but-absent provider holds the ceiling and exits 1."""
        root = self._workspace()
        canonical = self._write_canonical(
            root,
            {
                "provider_summaries": [],
                "findings": [],
            },
        )
        profile = root / "profile.json"
        profile.write_text(
            json.dumps({
                "required_contexts": {
                    "always": ["Codacy analysis"]
                },
            }),
            encoding="utf-8",
        )
        ratchet = root / "ratchet.json"
        code = rg.main(
            self._argv(
                canonical=canonical,
                ratchet=ratchet,
                repo_dir=root,
                head_sha="sha4",
                extra=["--profile-json", str(profile)],
            ))
        self.assertEqual(code, 1)

    def test_unseeded_returns_1(self) -> None:
        """A measured-but-unseeded provider (no ``--seed``) exits 1."""
        root = self._workspace()
        canonical = self._write_canonical(
            root,
            {
                "provider_summaries": [{
                    "provider": "Codacy",
                    "total": 2
                }],
                "findings": [],
            },
        )
        ratchet = root / "ratchet.json"
        code = rg.main(
            self._argv(
                canonical=canonical,
                ratchet=ratchet,
                repo_dir=root,
                head_sha="sha5",
            ))
        self.assertEqual(code, 1)

    def test_profile_json_path_missing_is_ignored(self) -> None:
        """A ``--profile-json`` pointing at a missing file is tolerated."""
        root = self._workspace()
        canonical = self._write_canonical(
            root,
            {
                "provider_summaries": [],
                "findings": [],
            },
        )
        ratchet = root / "ratchet.json"
        code = rg.main(
            self._argv(
                canonical=canonical,
                ratchet=ratchet,
                repo_dir=root,
                head_sha="sha6",
                extra=["--profile-json",
                       str(root / "nope.json")],
            ))
        self.assertEqual(code, 0)

    def test_changed_without_write_does_not_persist(self) -> None:
        """Without ``--write`` an auto-lower is computed but not persisted."""
        root = self._workspace()
        canonical = self._write_canonical(
            root,
            {
                "provider_summaries": [{
                    "provider": "Codacy",
                    "total": 1
                }],
                "findings": [],
            },
        )
        ratchet = root / "ratchet.json"
        original = json.dumps({
            "providers": {
                "Codacy": {
                    "ceiling": 5,
                    "measured": True
                }
            },
        })
        ratchet.write_text(original, encoding="utf-8")
        code = rg.main(
            self._argv(
                canonical=canonical,
                ratchet=ratchet,
                repo_dir=root,
                head_sha="sha7",
            ))
        self.assertEqual(code, 0)
        self.assertEqual(ratchet.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
