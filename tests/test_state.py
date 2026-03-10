"""Tests for iblai_infra.terraform.state — persistence and session management."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from iblai_infra.models import AWSCredentials, AuthMethod, InfraConfig, ProjectState
from iblai_infra.terraform.state import (
    clear_session,
    list_all_states,
    list_workspaces,
    load_state,
    save_session,
    save_state,
    workspace_dir,
)


# ---------------------------------------------------------------------------
# workspace_dir
# ---------------------------------------------------------------------------


class TestWorkspaceDir:
    def test_returns_correct_path(self, infra_config, workspace_root):
        ws = workspace_dir(infra_config)
        assert ws.name == "testproject-dev"
        assert ws.parent == workspace_root


# ---------------------------------------------------------------------------
# save_state / load_state
# ---------------------------------------------------------------------------


class TestSaveAndLoadState:
    def test_save_creates_file(self, project_state, workspace_root):
        ws = workspace_root / "testproject-dev"
        project_state.workspace_path = str(ws)

        path = save_state(project_state)
        assert path.exists()
        assert path.name == "state.json"

    def test_load_by_name(self, project_state, workspace_root):
        ws = workspace_root / "testproject-dev"
        project_state.workspace_path = str(ws)
        save_state(project_state)

        loaded = load_state("testproject")
        assert loaded is not None
        assert loaded.name == "testproject"
        assert loaded.status == "created"
        assert loaded.config.project_name == "testproject"

    def test_load_nonexistent_returns_none(self, workspace_root):
        assert load_state("nonexistent") is None

    def test_save_updates_timestamp(self, project_state, workspace_root):
        ws = workspace_root / "testproject-dev"
        project_state.workspace_path = str(ws)

        old_updated = project_state.updated_at
        save_state(project_state)
        assert project_state.updated_at >= old_updated

    def test_save_creates_directory(self, project_state, workspace_root):
        ws = workspace_root / "new-project"
        project_state.workspace_path = str(ws)

        save_state(project_state)
        assert ws.exists()
        assert (ws / "state.json").exists()

    def test_roundtrip_preserves_outputs(self, project_state, workspace_root):
        ws = workspace_root / "testproject-dev"
        project_state.workspace_path = str(ws)
        save_state(project_state)

        loaded = load_state("testproject")
        assert loaded.outputs["instance_public_ip"] == "54.123.45.67"
        assert loaded.outputs["alb_dns_name"] == "alb-123.us-east-1.elb.amazonaws.com"


# ---------------------------------------------------------------------------
# list_workspaces / list_all_states
# ---------------------------------------------------------------------------


class TestListWorkspaces:
    def test_empty_when_no_projects(self, workspace_root):
        assert list_workspaces() == []

    def test_lists_directories_with_state(self, project_state, workspace_root):
        ws = workspace_root / "project-a"
        project_state.workspace_path = str(ws)
        project_state.name = "project-a"
        save_state(project_state)

        workspaces = list_workspaces()
        assert len(workspaces) == 1
        assert workspaces[0].name == "project-a"

    def test_ignores_dirs_without_state_file(self, workspace_root):
        (workspace_root / "empty-dir").mkdir()
        assert list_workspaces() == []

    def test_multiple_workspaces_sorted(self, project_state, workspace_root):
        for name in ("project-c", "project-a", "project-b"):
            ws = workspace_root / name
            project_state.workspace_path = str(ws)
            project_state.name = name
            save_state(project_state)

        workspaces = list_workspaces()
        assert len(workspaces) == 3
        names = [w.name for w in workspaces]
        assert names == sorted(names)


class TestListAllStates:
    def test_loads_all_states(self, project_state, workspace_root):
        for name in ("proj-a", "proj-b"):
            ws = workspace_root / name
            project_state.workspace_path = str(ws)
            project_state.name = name
            save_state(project_state)

        states = list_all_states()
        assert len(states) == 2
        names = {s.name for s in states}
        assert names == {"proj-a", "proj-b"}

    def test_skips_corrupt_state_files(self, workspace_root):
        ws = workspace_root / "corrupt"
        ws.mkdir()
        (ws / "state.json").write_text("not valid json!")

        states = list_all_states()
        assert len(states) == 0


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------


class TestSessionPersistence:
    def test_save_and_load_profile_session(self, tmp_path):
        session_file = tmp_path / "session.json"
        with mock.patch("iblai_infra.terraform.state._SESSION_FILE", session_file):
            creds = AWSCredentials(
                method=AuthMethod.PROFILE,
                profile="myprofile",
                region="us-east-1",
                account_id="123456789012",
                arn="arn:aws:iam::123456789012:user/testuser",
            )
            save_session(creds)

            assert session_file.exists()
            data = json.loads(session_file.read_text())
            assert data["method"] == "profile"
            assert data["profile"] == "myprofile"
            assert data["region"] == "us-east-1"
            # Secret key should NOT be stored
            assert "secret_access_key" not in data

    def test_save_access_key_includes_key_id(self, tmp_path):
        session_file = tmp_path / "session.json"
        with mock.patch("iblai_infra.terraform.state._SESSION_FILE", session_file):
            creds = AWSCredentials(
                method=AuthMethod.ACCESS_KEY,
                access_key_id="AKIATEST",
                secret_access_key="secret",
                region="eu-west-1",
            )
            save_session(creds)

            data = json.loads(session_file.read_text())
            assert data["access_key_id"] == "AKIATEST"
            assert "secret_access_key" not in data

    def test_clear_session_removes_file(self, tmp_path):
        session_file = tmp_path / "session.json"
        session_file.write_text("{}")
        with mock.patch("iblai_infra.terraform.state._SESSION_FILE", session_file):
            clear_session()
            assert not session_file.exists()

    def test_clear_session_no_file(self, tmp_path):
        session_file = tmp_path / "no-such-file.json"
        with mock.patch("iblai_infra.terraform.state._SESSION_FILE", session_file):
            clear_session()  # Should not raise


# ---------------------------------------------------------------------------
# load_session — all auth method paths
# ---------------------------------------------------------------------------


class TestLoadSession:
    def test_load_profile_session(self, tmp_path):
        from iblai_infra.terraform.state import load_session

        session_file = tmp_path / "session.json"
        data = {
            "method": "profile",
            "profile": "myprofile",
            "region": "us-east-1",
            "account_id": "123",
            "arn": "arn:aws:iam::123:user/test",
        }
        session_file.write_text(json.dumps(data))

        mock_identity = mock.MagicMock()
        mock_identity.account_id = "123"
        mock_identity.arn = "arn:aws:iam::123:user/test"

        with (
            mock.patch("iblai_infra.terraform.state._SESSION_FILE", session_file),
            mock.patch("iblai_infra.providers.aws.validate_credentials", return_value=mock_identity),
        ):
            result = load_session()
            assert result is not None
            creds, identity = result
            assert creds.method == AuthMethod.PROFILE
            assert creds.profile == "myprofile"

    def test_load_access_key_returns_none(self, tmp_path):
        """Access key method cannot be restored (secret not stored)."""
        from iblai_infra.terraform.state import load_session

        session_file = tmp_path / "session.json"
        data = {
            "method": "access_key",
            "access_key_id": "AKIA",
            "region": "us-east-1",
        }
        session_file.write_text(json.dumps(data))

        with mock.patch("iblai_infra.terraform.state._SESSION_FILE", session_file):
            result = load_session()
            assert result is None

    def test_load_environment_with_env_vars(self, tmp_path):
        from iblai_infra.terraform.state import load_session

        session_file = tmp_path / "session.json"
        data = {"method": "environment", "region": "us-east-1"}
        session_file.write_text(json.dumps(data))

        mock_identity = mock.MagicMock()
        mock_identity.account_id = "456"
        mock_identity.arn = "arn:aws:iam::456:user/env"

        with (
            mock.patch("iblai_infra.terraform.state._SESSION_FILE", session_file),
            mock.patch("iblai_infra.providers.aws.has_env_credentials", return_value=True),
            mock.patch("iblai_infra.providers.aws.validate_credentials", return_value=mock_identity),
        ):
            result = load_session()
            assert result is not None

    def test_load_environment_no_env_vars(self, tmp_path):
        """Environment method fails if env vars are gone."""
        from iblai_infra.terraform.state import load_session

        session_file = tmp_path / "session.json"
        data = {"method": "environment", "region": "us-east-1"}
        session_file.write_text(json.dumps(data))

        with (
            mock.patch("iblai_infra.terraform.state._SESSION_FILE", session_file),
            mock.patch("iblai_infra.providers.aws.has_env_credentials", return_value=False),
        ):
            result = load_session()
            assert result is None

    def test_load_no_session_file(self, tmp_path):
        from iblai_infra.terraform.state import load_session

        session_file = tmp_path / "nonexistent.json"
        with mock.patch("iblai_infra.terraform.state._SESSION_FILE", session_file):
            assert load_session() is None

    def test_load_corrupt_session_deletes_file(self, tmp_path):
        from iblai_infra.terraform.state import load_session

        session_file = tmp_path / "session.json"
        session_file.write_text("not valid json!")
        with mock.patch("iblai_infra.terraform.state._SESSION_FILE", session_file):
            result = load_session()
            assert result is None
            assert not session_file.exists()

    def test_load_validation_failure_deletes_file(self, tmp_path):
        from iblai_infra.terraform.state import load_session

        session_file = tmp_path / "session.json"
        data = {
            "method": "profile",
            "profile": "expired",
            "region": "us-east-1",
        }
        session_file.write_text(json.dumps(data))

        with (
            mock.patch("iblai_infra.terraform.state._SESSION_FILE", session_file),
            mock.patch(
                "iblai_infra.providers.aws.validate_credentials",
                side_effect=ValueError("expired"),
            ),
        ):
            result = load_session()
            assert result is None
            assert not session_file.exists()


# ---------------------------------------------------------------------------
# load_state — edge cases
# ---------------------------------------------------------------------------


class TestLoadStateEdgeCases:
    def test_load_corrupt_state_file(self, workspace_root):
        """load_state skips corrupt state files and returns None."""
        ws = workspace_root / "corrupt-project"
        ws.mkdir()
        (ws / "state.json").write_text("{invalid json")
        assert load_state("corrupt-project") is None

    def test_load_wrong_name(self, project_state, workspace_root):
        ws = workspace_root / "testproject-dev"
        project_state.workspace_path = str(ws)
        save_state(project_state)
        assert load_state("different-name") is None
