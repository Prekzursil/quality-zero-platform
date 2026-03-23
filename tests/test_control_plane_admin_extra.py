from __future__ import absolute_import

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from scripts.quality import control_plane_admin


class ControlPlaneAdminExtraTests(unittest.TestCase):
    def test_parse_args_supports_admin_subcommands(self) -> None:
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
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            with patch.object(control_plane_admin, "parse_args", return_value=Namespace(
                repo_root=str(repo_root),
                command="enroll-repo",
                repo_slug="owner/repo",
                profile_id="example",
                stack="python-web",
                rollout="phase2-wave0",
                default_branch="main",
            )), patch.object(control_plane_admin, "enroll_repo") as enroll_repo_mock:
                self.assertEqual(control_plane_admin.main(), 0)
            enroll_repo_mock.assert_called_once()
            request = enroll_repo_mock.call_args.kwargs["request"]
            self.assertEqual(request.repo_slug, "owner/repo")

            with patch.object(control_plane_admin, "parse_args", return_value=Namespace(
                repo_root=str(repo_root),
                command="set-required-context",
                profile_id="example",
                context_set="target",
                context_name="Coverage 100 Gate",
                present="true",
            )), patch.object(control_plane_admin, "set_required_context") as set_required_context_mock:
                self.assertEqual(control_plane_admin.main(), 0)
            mutation = set_required_context_mock.call_args.kwargs["mutation"]
            self.assertTrue(mutation.present)

            with patch.object(control_plane_admin, "parse_args", return_value=Namespace(
                repo_root=str(repo_root),
                command="set-coverage-mode",
                profile_id="example",
                event_name="default",
                mode="enforce",
            )), patch.object(control_plane_admin, "set_coverage_mode") as set_coverage_mock:
                self.assertEqual(control_plane_admin.main(), 0)
            self.assertEqual(set_coverage_mock.call_args.kwargs["event_name"], "default")
