"""Tests for iblai_infra.terraform.state — persistence and session management."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from iblai_infra.models import AWSCredentials, AuthMethod, InfraConfig, ProjectState
from iblai_infra.terraform.state import (
    _INGRESS_FILE,
    _LOCKS_DIR,
    add_ingress,
    claim_ingress,
    clear_session,
    configure_ingress_lock,
    get_ingress_status,
    list_all_states,
    list_workspaces,
    load_ingress,
    load_ingress_registry,
    load_state,
    release_ingress_lock,
    remove_ingress,
    save_ingress,
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


# ---------------------------------------------------------------------------
# Ingress registry
# ---------------------------------------------------------------------------


class TestIngress:
    @pytest.fixture(autouse=True)
    def _patch_ingress_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "iblai_infra.terraform.state._INGRESS_FILE", tmp_path / "ingress.json"
        )

    def test_load_empty(self):
        assert load_ingress() == []

    def test_add_and_load(self):
        entry = add_ingress("stg1", "stg1.example.com")
        assert entry.name == "stg1"
        entries = load_ingress()
        assert len(entries) == 1
        assert entries[0].domain == "stg1.example.com"

    def test_add_multiple(self):
        add_ingress("stg1", "stg1.example.com")
        add_ingress("stg2", "stg2.example.com")
        assert len(load_ingress()) == 2

    def test_add_duplicate_raises(self):
        add_ingress("stg1", "stg1.example.com")
        with pytest.raises(ValueError, match="already exists"):
            add_ingress("stg1", "stg1-other.example.com")

    def test_remove_existing(self):
        add_ingress("stg1", "stg1.example.com")
        add_ingress("stg2", "stg2.example.com")
        assert remove_ingress("stg1") is True
        entries = load_ingress()
        assert len(entries) == 1
        assert entries[0].name == "stg2"

    def test_remove_nonexistent(self):
        assert remove_ingress("nope") is False

    def test_load_corrupt_file(self, tmp_path):
        (tmp_path / "ingress.json").write_text("{bad json")
        assert load_ingress() == []

    def test_save_and_load_roundtrip(self):
        from iblai_infra.models import IngressEntry
        entries = [
            IngressEntry(name="a", domain="a.example.com"),
            IngressEntry(name="b", domain="b.example.com"),
        ]
        save_ingress(entries)
        loaded = load_ingress()
        assert len(loaded) == 2
        assert loaded[0].name == "a"
        assert loaded[1].name == "b"

    def test_backward_compat_list_format(self, tmp_path):
        """Old ingress.json format (bare list) is auto-migrated."""
        import json
        (tmp_path / "ingress.json").write_text(json.dumps([
            {"name": "old1", "domain": "old1.example.com", "created_at": "2026-01-01T00:00:00Z"},
        ]))
        entries = load_ingress()
        assert len(entries) == 1
        assert entries[0].name == "old1"
        # Lock config defaults to local
        reg = load_ingress_registry()
        assert reg.lock.backend == "local"

    def test_configure_lock_backend(self):
        add_ingress("stg1", "stg1.example.com")
        configure_ingress_lock(bucket="my-bucket", prefix="my-prefix")
        reg = load_ingress_registry()
        assert reg.lock.backend == "s3"
        assert reg.lock.bucket == "my-bucket"
        assert reg.lock.prefix == "my-prefix"
        # Entries preserved
        assert len(reg.entries) == 1

    def test_add_preserves_lock_config(self):
        configure_ingress_lock(bucket="my-bucket")
        add_ingress("stg1", "stg1.example.com")
        reg = load_ingress_registry()
        assert reg.lock.bucket == "my-bucket"
        assert len(reg.entries) == 1

    def test_remove_preserves_lock_config(self):
        add_ingress("stg1", "stg1.example.com")
        configure_ingress_lock(bucket="my-bucket")
        remove_ingress("stg1")
        reg = load_ingress_registry()
        assert reg.lock.bucket == "my-bucket"
        assert len(reg.entries) == 0


# ---------------------------------------------------------------------------
# Ingress locks (local backend)
# ---------------------------------------------------------------------------


class TestIngressLocks:
    @pytest.fixture(autouse=True)
    def _patch_paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "iblai_infra.terraform.state._INGRESS_FILE", tmp_path / "ingress.json"
        )
        monkeypatch.setattr(
            "iblai_infra.terraform.state._LOCKS_DIR", tmp_path / "locks"
        )

    def test_claim_first_free(self):
        add_ingress("stg1", "stg1.example.com")
        add_ingress("stg2", "stg2.example.com")
        result = claim_ingress(claimed_by="test-run")
        assert result == ("stg1", "stg1.example.com")

    def test_claim_specific(self):
        add_ingress("stg1", "stg1.example.com")
        add_ingress("stg2", "stg2.example.com")
        result = claim_ingress(name="stg2", claimed_by="test-run")
        assert result == ("stg2", "stg2.example.com")

    def test_claim_already_claimed(self):
        add_ingress("stg1", "stg1.example.com")
        claim_ingress(name="stg1", claimed_by="run-1")
        result = claim_ingress(name="stg1", claimed_by="run-2")
        assert result is None

    def test_claim_skips_occupied(self):
        add_ingress("stg1", "stg1.example.com")
        add_ingress("stg2", "stg2.example.com")
        claim_ingress(name="stg1", claimed_by="run-1")
        result = claim_ingress(claimed_by="run-2")
        assert result == ("stg2", "stg2.example.com")

    def test_claim_all_occupied(self):
        add_ingress("stg1", "stg1.example.com")
        claim_ingress(name="stg1", claimed_by="run-1")
        result = claim_ingress(claimed_by="run-2")
        assert result is None

    def test_claim_no_entries(self):
        result = claim_ingress(claimed_by="test")
        assert result is None

    def test_claim_nonexistent_name(self):
        add_ingress("stg1", "stg1.example.com")
        result = claim_ingress(name="nope", claimed_by="test")
        assert result is None

    def test_release(self):
        add_ingress("stg1", "stg1.example.com")
        claim_ingress(name="stg1", claimed_by="run-1")
        assert release_ingress_lock("stg1") is True
        # Can claim again
        result = claim_ingress(name="stg1", claimed_by="run-2")
        assert result == ("stg1", "stg1.example.com")

    def test_release_not_claimed(self):
        assert release_ingress_lock("stg1") is False

    def test_status(self):
        add_ingress("stg1", "stg1.example.com")
        add_ingress("stg2", "stg2.example.com")
        claim_ingress(name="stg1", claimed_by="run-1")

        statuses = get_ingress_status()
        assert len(statuses) == 2
        # stg1 is claimed
        assert statuses[0][0].name == "stg1"
        assert statuses[0][1] is not None
        assert statuses[0][1]["claimed_by"] == "run-1"
        # stg2 is free
        assert statuses[1][0].name == "stg2"
        assert statuses[1][1] is None

    def test_status_empty(self):
        assert get_ingress_status() == []
