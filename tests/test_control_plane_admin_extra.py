"""Test control plane admin extra."""

from __future__ import absolute_import

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from scripts.quality import control_plane_admin


class ControlPlaneAdminExtraTests(unittest.TestCase):
    """Control Plane Admin Extra Tests."""

    def _run_main_for_args(self, repo_root: Path, **kwargs):
        """Run one admin command and return the patched mutation helper."""
        handler_name = kwargs.pop("handler_name")
        with patch.object(
            control_plane_admin,
            "parse_args",
            return_value=Namespace(repo_root=str(repo_root), **kwargs),
        ), patch.object(control_plane_admin, handler_name) as handler_mock:
            self.assertEqual(control_plane_admin.main(), 0)
        return handler_mock

    def test_parse_args_supports_admin_subcommands(self) -> None:
        """Cover parse args supports admin subcommands."""
        with patch(
            "sys.argv",
            [
                "control_plane_admin.py",
                "set-scanner",
                "--profile-id",
                "example",
                "--scanner",
                "sonar",
                "--enabled",
                "true",
            ],
        ):
            args = control_plane_admin.parse_args()
        self.assertEqual(args.command, "set-scanner")
        self.assertEqual(args.enabled, "true")

        with patch(
            "sys.argv",
            [
                "control_plane_admin.py",
                "set-required-context",
                "--profile-id",
                "example",
                "--context-set",
                "target",
                "--context-name",
                "Coverage 100 Gate",
                "--present",
                "false",
            ],
        ):
            args = control_plane_admin.parse_args()
        self.assertEqual(args.command, "set-required-context")
        self.assertEqual(args.context_name, "Coverage 100 Gate")

    def test_main_dispatches_to_expected_mutation_helpers(self) -> None:
        """Cover main dispatches to expected mutation helpers."""
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            enroll_repo_mock = self._run_main_for_args(
                repo_root,
                handler_name="enroll_repo",
                command="enroll-repo",
                repo_slug="owner/repo",
                profile_id="example",
                stack="python-web",
                rollout="phase2-wave0",
                default_branch="main",
            )
            enroll_repo_mock.assert_called_once()
            request = enroll_repo_mock.call_args.kwargs["request"]
            self.assertEqual(request.repo_slug, "owner/repo")

            set_required_context_mock = self._run_main_for_args(
                repo_root,
                handler_name="set_required_context",
                command="set-required-context",
                profile_id="example",
                context_set="target",
                context_name="Coverage 100 Gate",
                present="true",
            )
            mutation = set_required_context_mock.call_args.kwargs["mutation"]
            self.assertTrue(mutation.present)

            set_coverage_mock = self._run_main_for_args(
                repo_root,
                handler_name="set_coverage_mode",
                command="set-coverage-mode",
                profile_id="example",
                event_name="default",
                mode="enforce",
            )
            self.assertEqual(
                set_coverage_mock.call_args.kwargs["event_name"], "default"
            )
