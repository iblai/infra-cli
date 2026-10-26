"""Tests for iblai_infra.env_multinode: .env -> multi-node state and run vars.

Network-free: no SSH, no GitHub (CLI_OPS_RELEASE_TAG is always set, so the
pinned tag is never resolved).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import typer

from iblai_infra.env_multinode import (
    install_config,
    load_or_create_state,
    platform_config,
    sandbox_vars,
    store_secret_vars,
    topology_of,
    topology_vars,
    wheel_vars,
)
from iblai_infra.models import DeploymentType, MultiNodeTopology, StoreLocation

EXIT = (SystemExit, typer.Exit)


@pytest.fixture(autouse=True)
def _keys_ok():
    with patch("iblai_infra.env_multinode.validate_key_permissions", return_value=True), \
         patch("iblai_infra.env_setup.validate_key_permissions", return_value=True):
        yield


@pytest.fixture
def key(tmp_path):
    k = tmp_path / "id_test"
    k.write_text("not a real key")
    return k


def _env(key, **overrides) -> dict[str, str]:
    env = {
        "PROJECT_NAME": "demo",
        "BASE_DOMAIN": "Example.COM",
        "SSH_PRIVATE_KEY_PATH": str(key),
        "HUB_HOST": "10.0.1.10",
        "APP_HOSTS": "10.0.1.11",
        "STORES_HOST": "10.0.2.10",
        "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "GIT_TOKEN": "test-pat-value",
        "CLI_OPS_RELEASE_TAG": "7.24.0",
        "PROD_IMAGES_TAG": "1.277.0",
        "ADMIN_USERNAME": "platform_admin",
        "ADMIN_EMAIL": "admin@example.com",
        "ADMIN_PASSWORD": "a-long-password",
        "OPENAI_API_KEY": "sk-test",
    }
    env.update(overrides)
    return env


MANAGED = dict(
    MYSQL="managed", MYSQL_HOST="mysql.example.internal", MYSQL_ROOT_PASSWORD="mysql-secret-value",
    POSTGRES="managed", POSTGRES_HOST="pg.example.internal", POSTGRES_ADMIN_PASSWORD="pg-secret-value",
    REDIS_EDX="managed", REDIS_EDX_HOST="redis-edx.example.internal", REDIS_EDX_PASSWORD="redis-edx-secret",
    REDIS_DM="managed", REDIS_DM_HOST="redis-dm.example.internal", REDIS_DM_PASSWORD="redis-dm-secret",
)


class TestCreate:
    def test_init_creates_a_multi_node_project(self, workspace_root, key):
        state = load_or_create_state(_env(key), create=True)
        assert state.config.deployment_type == DeploymentType.MULTI_NODE
        assert state.provider == "multi-node"
        assert state.config.dns.base_domain == "example.com"
        # Features target one host: the hub.
        assert state.outputs["instance_public_ip"] == "10.0.1.10"
        topo = topology_of(state)
        assert topo.node_ids == {"10.0.1.10": "demo-app-1", "10.0.1.11": "demo-app-2", "10.0.2.10": "demo-stores"}
        assert topo.sandbox_domain == "sandbox.example.com"

    def test_other_commands_need_the_project(self, workspace_root, key):
        with pytest.raises(EXIT):
            load_or_create_state(_env(key))

    def test_stores_default_to_the_stores_node(self, workspace_root, key):
        topo = topology_of(load_or_create_state(_env(key), create=True))
        assert {topo.mysql, topo.postgres, topo.redis_edx, topo.redis_dm} == {StoreLocation.STORES}
        assert topo.postgres_endpoint is None

    def test_managed_stores_record_endpoints_not_credentials(self, workspace_root, key):
        state = load_or_create_state(_env(key, **MANAGED, POSTGRES_PORT="6432"), create=True)
        topo = topology_of(state)
        assert topo.postgres == StoreLocation.MANAGED
        assert topo.postgres_endpoint.host == "pg.example.internal"
        assert topo.postgres_endpoint.port == 6432
        assert topo.mysql_endpoint.username == "admin"
        on_disk = (workspace_root / "demo-multinode" / "state.json").read_text()
        for secret in ("mysql-secret-value", "pg-secret-value", "redis-edx-secret", "redis-dm-secret",
                       "test-pat-value", "a-long-password", "wJalrXUtnFEMI"):
            assert secret not in on_disk

    def test_managed_store_needs_a_host(self, workspace_root, key):
        with pytest.raises(EXIT):
            load_or_create_state(_env(key, POSTGRES="managed"), create=True)

    def test_unknown_location_is_rejected(self, workspace_root, key):
        with pytest.raises(EXIT):
            load_or_create_state(_env(key, MYSQL="rds"), create=True)

    def test_a_host_holds_one_role(self, workspace_root, key):
        with pytest.raises(EXIT):
            load_or_create_state(_env(key, STORES_HOST="10.0.1.11"), create=True)

    def test_an_existing_single_server_project_is_refused(self, workspace_root, key, project_state):
        from iblai_infra.terraform.state import save_state

        project_state.name = "demo"
        project_state.workspace_path = str(workspace_root / "demo-dev")
        save_state(project_state)
        with pytest.raises(EXIT):
            load_or_create_state(_env(key), create=True)


class TestReload:
    def test_new_app_hosts_join_with_new_node_ids(self, workspace_root, key):
        load_or_create_state(_env(key), create=True)
        state = load_or_create_state(_env(key, APP_HOSTS="10.0.1.11,10.0.1.12"))
        topo = topology_of(state)
        assert topo.apps == ["10.0.1.11", "10.0.1.12"]
        assert topo.node_ids["10.0.1.11"] == "demo-app-2"
        assert topo.node_ids["10.0.1.12"] == "demo-app-3"

    def test_node_ids_never_change(self, workspace_root, key):
        load_or_create_state(_env(key), create=True)
        state = load_or_create_state(_env(key, NODE_ID_PREFIX="other"))
        assert topology_of(state).node_ids["10.0.1.10"] == "demo-app-1"

    @pytest.mark.parametrize("key_name, value", [("HUB_HOST", "10.0.9.9"), ("STORES_HOST", "10.0.9.9")])
    def test_the_hub_and_stores_node_cannot_move(self, workspace_root, key, key_name, value):
        load_or_create_state(_env(key), create=True)
        with pytest.raises(EXIT):
            load_or_create_state(_env(key, **{key_name: value}))


class TestRunVars:
    def test_topology_vars_for_a_managed_layout(self, workspace_root, key):
        topo = topology_of(load_or_create_state(_env(key, **MANAGED), create=True))
        v = topology_vars(topo)
        assert v["postgres_location"] == "managed"
        assert v["postgres_host"] == "pg.example.internal"
        assert v["postgres_admin_username"] == "postgres"
        assert v["mysql_root_username"] == "admin"
        assert v["redis_dm_tls"] is True
        assert not any("password" in k for k in v)

    def test_store_secrets_come_from_the_env(self, workspace_root, key):
        env = _env(key, **MANAGED)
        topo = topology_of(load_or_create_state(env, create=True))
        s = store_secret_vars(env, topo)
        assert s == {
            "mysql_root_password": "mysql-secret-value",
            "postgres_admin_password": "pg-secret-value",
            "redis_edx_password": "redis-edx-secret",
            "redis_dm_password": "redis-dm-secret",
        }

    @pytest.mark.parametrize("missing", ["MYSQL_ROOT_PASSWORD", "POSTGRES_ADMIN_PASSWORD", "REDIS_DM_PASSWORD"])
    def test_a_managed_store_needs_its_credentials(self, workspace_root, key, missing):
        env = _env(key, **MANAGED)
        topo = topology_of(load_or_create_state(env, create=True))
        env.pop(missing)
        with pytest.raises(EXIT):
            store_secret_vars(env, topo)

    def test_stores_on_the_node_need_no_credentials(self, workspace_root, key):
        env = _env(key)
        assert store_secret_vars(env, topology_of(load_or_create_state(env, create=True))) == {}

    def test_wheels_both_or_neither(self, tmp_path):
        w = tmp_path / "ibl_cli-7.30.0-py3-none-any.whl"
        w.write_text("")
        with pytest.raises(EXIT):
            wheel_vars({"CLI_OPS_WHEEL": str(w)})
        both = wheel_vars({"CLI_OPS_WHEEL": str(w), "PROD_IMAGES_WHEEL": str(w)})
        assert both["cli_ops_wheel"] == str(w)

    def test_sandbox_vars(self, workspace_root, key):
        state = load_or_create_state(_env(key), create=True)
        v = sandbox_vars({"SANDBOX_VM_MENTORS": "main:agentAI, acme:Tutor"}, state)
        assert v["sandbox_tenant"] == "demo-dm"
        assert v["sandbox_vm_mentors"] == ["main:agentAI", "acme:Tutor"]
        assert v["sandbox_egress_profiles"] == ["none"]
        with pytest.raises(EXIT):
            sandbox_vars({"SANDBOX_VM_MENTORS": "agentAI"}, state)


class TestConfigs:
    def test_platform_config_targets_the_hub(self, workspace_root, key):
        env = _env(key)
        state = load_or_create_state(env, create=True)
        config = platform_config(env, state)
        assert config.target_host == "10.0.1.10"
        assert config.base_domain == "example.com"
        assert config.cli_ops_release_tag == "7.24.0"

    def test_platform_config_with_wheels_needs_no_token(self, workspace_root, key, tmp_path):
        w = tmp_path / "w.whl"
        w.write_text("")
        env = _env(key, CLI_OPS_WHEEL=str(w), PROD_IMAGES_WHEEL=str(w))
        env.pop("GIT_TOKEN")
        env.pop("CLI_OPS_RELEASE_TAG")
        state = load_or_create_state(env, create=True)
        assert platform_config(env, state).git_access_token == ""

    def test_setup_env_still_refuses_multi_node(self, workspace_root, key):
        from iblai_infra.env_setup import build_setup_config_from_env

        env = _env(key)
        state = load_or_create_state(env, create=True)
        with pytest.raises(EXIT):
            build_setup_config_from_env(env, state=state)

    def test_sandbox_install_prefers_its_own_keys(self, workspace_root, key):
        env = _env(key, SANDBOX_HOST="10.0.3.10", SANDBOX_CLI_OPS_RELEASE_TAG="7.30.0",
                   SANDBOX_PROD_IMAGES_TAG="1.312.0", SANDBOX_AWS_ACCESS_KEY_ID="AKIASANDBOXEXAMPLE")
        state = load_or_create_state(env, create=True)
        config = install_config(env, state, prefix="SANDBOX_", target_host="10.0.3.10")
        assert config.cli_ops_release_tag == "7.30.0"
        assert config.prod_images_tag == "1.312.0"
        assert config.aws_access_key_id == "AKIASANDBOXEXAMPLE"
        # No SANDBOX_ secret: falls back to the platform's.
        assert config.aws_secret_access_key == env["AWS_SECRET_ACCESS_KEY"]
        assert config.target_host == "10.0.3.10"


class TestTopologyModel:
    def test_node_ids_skip_taken_numbers(self):
        t = MultiNodeTopology(hub="a", apps=["b"], stores="s", node_id_prefix="p",
                              node_ids={"a": "p-app-1", "x": "p-app-2"})
        t.assign_node_ids()
        assert t.node_ids["b"] == "p-app-3"

    def test_managed_without_endpoint_is_invalid(self):
        with pytest.raises(ValueError):
            MultiNodeTopology(hub="a", stores="s", node_id_prefix="p", mysql=StoreLocation.MANAGED)
