from __future__ import absolute_import

import tempfile
import unittest
from pathlib import Path

import yaml
from tests.control_plane_support import ROOT

from scripts.quality.control_plane import active_required_contexts, load_inventory, load_repo_profile
from scripts.quality.render_repo_baseline import (
    LEGACY_ZERO_WORKFLOW_FILES,
    render_codeql_wrapper,
    render_dependabot_config,
    render_qlty_config,
    render_repo_baseline,
    render_security_policy,
)

#: Governed repos MEASURED to run CodeQL via GitHub **default setup** rather than a
#: managed ``codeql.yml`` wrapper, and therefore physically unable to report the
#: advanced-setup ``codeql / CodeQL`` context. Membership is a measurement, not a
#: preference: probe ``GET /repos/{slug}/actions/workflows`` for
#: ``.github/workflows/codeql.yml`` (absent, or ``disabled_manually``) together with
#: ``dynamic/github-code-scanning/codeql`` (``active``), then corroborate against the
#: check-run names actually emitted on ``main``.
#:
#: Measured 2026-08-11 across all 18 inventory members. SIX are on default setup:
#:
#:   * ``Prekzursil/tracelines``            -- codeql.yml ABSENT            (registered)
#:   * ``Prekzursil/WebCoder``              -- codeql.yml disabled_manually (PENDING)
#:   * ``Prekzursil/Reframe``               -- codeql.yml ABSENT            (PENDING)
#:   * ``Prekzursil/env-inspector``         -- codeql.yml disabled_manually (PENDING)
#:   * ``Prekzursil/PrimariRo``             -- codeql.yml ABSENT            (PENDING)
#:   * ``Prekzursil/agent-skills-toolchain``-- codeql.yml ABSENT            (PENDING)
#:
#: The five PENDING entries are deliberately NOT registered yet. WebCoder,
#: env-inspector and Reframe inherit ``codeql / CodeQL`` from the shared
#: ``profiles/stacks/quality-zero-phase1-common.yml`` stack, so de-listing it is a
#: cohort-wide change that the ADDITIVE-ONLY lean-gate charter defers to a
#: coordinated fleet runbook. Add a slug here in the same commit that fixes its
#: profile -- the gate then makes the phantom impossible to reintroduce.
CODEQL_DEFAULT_SETUP_REPOS = frozenset({"Prekzursil/tracelines"})

#: The ADVANCED-setup spelling, lowercased for comparison. GitHub default setup
#: never emits it, so requiring it in a default-setup repo is an unreportable
#: context that strands the branch permanently red.
ADVANCED_ONLY_CODEQL_CONTEXT = "codeql / codeql"


class SecurityBaselineProfileTests(unittest.TestCase):
    """Protect the managed CodeQL, Dependabot, SECURITY, and QLTY baseline contract."""

    @staticmethod
    def _enforces_codeql(contexts) -> bool:
        """Is CodeQL a REQUIRED context, under either of GitHub's two setups?

        There are two supported ways to run CodeQL, and they emit different check
        names:

          * ADVANCED setup -- a `codeql.yml` workflow in the repo -- emits
            ``codeql / CodeQL``.
          * DEFAULT setup -- GitHub-managed, configured in the Security tab -- emits
            an aggregate ``CodeQL`` check from the GitHub Advanced Security app
            (app 57789) plus, on some events only, one ``analyze (<language>)`` per
            analysed language. It NEVER emits the workflow-style name.

        This assertion previously accepted only the advanced spelling, which silently
        wedges any repo migrated to default setup: the required context can never be
        reported, so no PR can ever merge. That is not hypothetical -- Prekzursil/WebCoder
        required ``codeql / CodeQL`` while emitting only ``CodeQL`` / ``Analyze (...)``,
        and was unmergeable from the day its CodeQL moved to default setup until the
        classic protection was corrected on 2026-08-11.

        The bare aggregate ``CodeQL`` is accepted because it is the spelling the fleet
        actually deploys for default-setup repos -- measured 2026-08-11,
        DevExtreme-Filter-Go-Language's classic protection requires ``CodeQL``@57789 and
        env-inspector's active ruleset requires ``CodeQL`` with no app pin. It is also
        the only default-setup spelling that is reliably present on a pull_request
        event: across WebCoder's 16 open PR heads, ``CodeQL`` appeared 16/16 while
        ``Analyze (<lang>)`` appeared 1/16.

        This is a recognition fix, not a relaxation: a repo that requires NONE of the
        three forms still fails, and :func:`test_default_setup_repos_never_require_the_advanced_codeql_context`
        additionally forbids the advanced spelling wherever it is known to be
        unreportable. Match is case-insensitive because the ruleset context and the
        emitted check-run name differ in capitalisation (``analyze`` vs ``Analyze``).
        """
        lowered = {str(c).lower() for c in contexts}
        if ADVANCED_ONLY_CODEQL_CONTEXT in lowered or "codeql" in lowered:
            return True
        return any(c.startswith("analyze (") for c in lowered)

    def test_all_governed_repos_declare_codeql_and_dependabot(self) -> None:
        """Every enrolled repo should expose the managed security-baseline metadata."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")

        for entry in inventory["repos"]:
            profile = load_repo_profile(inventory, entry["slug"])
            self.assertTrue(profile["codeql"]["enabled"], entry["slug"])
            self.assertTrue(profile["codeql"]["languages"], entry["slug"])
            self.assertTrue(profile["dependabot"]["enabled"], entry["slug"])
            for event in ("push", "ruleset"):
                contexts = active_required_contexts(profile, event_name=event)
                self.assertTrue(
                    self._enforces_codeql(contexts),
                    f"{entry['slug']} ({event}): no CodeQL context is required under "
                    f"either setup (advanced 'codeql / CodeQL' or default "
                    f"'analyze (<lang>)'); got {sorted(contexts)}",
                )
            self.assertTrue(
                self._enforces_codeql(profile["required_contexts"]["target"]),
                f"{entry['slug']} (target): no CodeQL context is required under either "
                f"setup; got {sorted(profile['required_contexts']['target'])}",
            )

    def test_default_setup_repos_never_require_the_advanced_codeql_context(self) -> None:
        """A default-setup repo must not require a context its CodeQL cannot emit.

        ``test_all_governed_repos_declare_codeql_and_dependabot`` only asks whether
        *some* CodeQL context is required. It accepts ``codeql / CodeQL`` from any
        repo, including repos whose ``codeql.yml`` is absent or disabled -- which is
        exactly how a phantom context survives review. Measured 2026-08-11 on
        Prekzursil/WebCoder: ``codeql / CodeQL`` appeared on **0 of 16** open PR heads
        while ``CodeQL`` appeared on **16 of 16**, and the unreportable context alone
        held every one of those PRs at ``mergeStateStatus: BLOCKED`` -- including the
        PR that clears the repo's critical websocket-driver advisory.

        So for every repo in :data:`CODEQL_DEFAULT_SETUP_REPOS` this asserts both
        halves: the advanced-only spelling is absent, AND a default-setup spelling is
        present, so the check is a substitution rather than a deletion.
        """
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        registered = {entry["slug"] for entry in inventory["repos"]}
        self.assertLessEqual(
            CODEQL_DEFAULT_SETUP_REPOS,
            registered,
            "CODEQL_DEFAULT_SETUP_REPOS names a slug that is not in the inventory",
        )

        for slug in sorted(CODEQL_DEFAULT_SETUP_REPOS):
            profile = load_repo_profile(inventory, slug)
            for event in ("push", "ruleset"):
                contexts = active_required_contexts(profile, event_name=event)
                lowered = {str(context).lower() for context in contexts}
                self.assertNotIn(
                    ADVANCED_ONLY_CODEQL_CONTEXT,
                    lowered,
                    f"{slug} ({event}) runs CodeQL via GitHub default setup, which never "
                    f"emits 'codeql / CodeQL'; requiring it strands main permanently red. "
                    f"Require the aggregate 'CodeQL' context instead; got {sorted(contexts)}",
                )
                self.assertTrue(
                    self._enforces_codeql(contexts),
                    f"{slug} ({event}): the advanced context was removed without putting a "
                    f"default-setup CodeQL context in its place; got {sorted(contexts)}",
                )

    def test_render_dependabot_config_includes_github_actions_and_repo_updates(self) -> None:
        """Dependabot rendering should include repo ecosystems plus github-actions."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/Reframe")

        rendered = yaml.safe_load(render_dependabot_config(profile))
        updates = rendered["updates"]
        pairs = {(item["package-ecosystem"], item["directory"]) for item in updates}

        self.assertIn(("github-actions", "/"), pairs)
        self.assertIn(("npm", "/apps/web"), pairs)
        self.assertIn(("npm", "/apps/desktop"), pairs)
        self.assertIn(("cargo", "/apps/desktop/src-tauri"), pairs)
        self.assertIn(("pip", "/apps/api"), pairs)

    def test_render_codeql_wrapper_pins_requested_controller_sha(self) -> None:
        """Repo CodeQL wrappers must use immutable controller refs."""
        rendered = render_codeql_wrapper(
            repo_slug="Prekzursil/WebCoder", platform_release_sha="0123456789abcdef0123456789abcdef01234567"
        )
        self.assertIn(
            "Prekzursil/quality-zero-platform/.github/workflows/reusable-codeql.yml@0123456789abcdef0123456789abcdef01234567",
            rendered,
        )
        self.assertIn("merge_group:", rendered)
        self.assertIn('cron: "23 3 * * 1"', rendered)

    def test_render_codeql_wrapper_uses_local_reusable_for_controller_repo(self) -> None:
        """The controller repo should use its local reusable workflow and current ref."""
        rendered = render_codeql_wrapper(
            repo_slug="Prekzursil/quality-zero-platform",
            platform_release_sha="0123456789abcdef0123456789abcdef01234567",
        )
        self.assertIn("uses: ./.github/workflows/reusable-codeql.yml", rendered)
        self.assertIn("platform_repository: ${{ github.repository }}", rendered)
        self.assertIn(
            "platform_ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            rendered,
        )

    def test_render_security_policy_uses_repo_advisory_url(self) -> None:
        """SECURITY.md should point at the repo's private advisory entrypoint."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/WebCoder")

        rendered = render_security_policy(profile)
        self.assertIn(
            "<https://github.com/Prekzursil/WebCoder/security/advisories/new>",
            rendered,
        )
        self.assertIn("@Prekzursil", rendered)

    def test_render_qlty_config_blocks_smells_for_governed_repos(self) -> None:
        """Managed QLTY config should be the same minimal block-mode baseline."""
        rendered = render_qlty_config()
        self.assertIn('config_version = "0"', rendered)
        self.assertIn('name = "default"', rendered)
        self.assertIn('mode = "block"', rendered)

    def test_render_repo_baseline_removes_legacy_zero_workflows(self) -> None:
        """Baseline rendering should delete superseded repo-local zero workflows."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        profile = load_repo_profile(inventory, "Prekzursil/pbinfo-get-unsolved")

        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            workflows = repo_root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            for filename in LEGACY_ZERO_WORKFLOW_FILES:
                (workflows / filename).write_text("name: legacy\n", encoding="utf-8")

            render_repo_baseline(
                profile=profile,
                repo_root=repo_root,
                platform_release_sha="0123456789abcdef0123456789abcdef01234567",
            )

            for filename in LEGACY_ZERO_WORKFLOW_FILES:
                self.assertFalse((workflows / filename).exists(), filename)
            self.assertTrue((repo_root / ".qlty" / "qlty.toml").exists())
