"""Tests for `iblai infra spa` — cloning a deployed SPA."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from iblai_infra.features.spa import (
    CLONE_PORT_BASE,
    CLONE_TAGS,
    REMOVE_TAGS,
    STOCK_SPAS,
    discover_spas,
    next_free_port,
    spa_clone,
    spa_remove,
)
from iblai_infra.models import (
    AWSCredentials,
    AuthMethod,
    CertificateConfig,
    CertMethod,
    ComputeConfig,
    DNSConfig,
    Environment,
    InfraConfig,
    NetworkConfig,
    ProjectState,
    SSHConfig,
    SSHKeyMethod,
    SetupConfig,
)


def _state(**kwargs) -> ProjectState:
    cfg = InfraConfig(
        project_name="acme",
        environment=Environment.PROD,
        credentials=AWSCredentials(
            method=AuthMethod.ACCESS_KEY, region="us-east-1",
            access_key_id="AK", secret_access_key="SK",
        ),
        network=NetworkConfig(vpn_ip="1.2.3.4"),
        compute=ComputeConfig(),
        ssh=SSHConfig(
            method=SSHKeyMethod.GENERATE, key_name="k",
            private_key_path=Path("/tmp/key.pem"),
        ),
        certificates=CertificateConfig(method=CertMethod.NONE),
        dns=DNSConfig(base_domain="acme.example.com"),
    )
    defaults = dict(
        name="acme", config=cfg, outputs={"instance_public_ip": "192.0.2.10"},
        workspace_path="/tmp/ws", status="created", setup_status="completed",
    )
    defaults.update(kwargs)
    return ProjectState(**defaults)


DEPLOYED = {"auth": 5000, "mentor": 5001, "skills": 5002, "os": 5005}


class _Applied:
    def __init__(self):
        self.tags = None
        self.extra = None

    def __call__(self, state, config, tags, labels, description, extra_vars=None):
        self.tags = tags
        self.extra = extra_vars
        return True


@pytest.fixture
def applied(monkeypatch):
    cap = _Applied()
    monkeypatch.setattr("iblai_infra.features.spa.apply_feature", cap)
    monkeypatch.setattr("iblai_infra.terraform.state.load_state", lambda name: _state())
    monkeypatch.setattr("iblai_infra.features.spa.discover_spas", lambda st: dict(DEPLOYED))
    # confirm prompts default to yes
    q = MagicMock()
    q.return_value.ask.return_value = True
    monkeypatch.setattr("questionary.confirm", q)
    return cap


# ---------------------------------------------------------------------------
# Port allocation
# ---------------------------------------------------------------------------


class TestPortAllocation:
    def test_first_clone_takes_the_base_port(self):
        assert next_free_port(DEPLOYED) == CLONE_PORT_BASE

    def test_skips_ports_already_taken_by_clones(self):
        used = dict(DEPLOYED, **{"a": 5060, "b": 5061})
        assert next_free_port(used) == 5062

    def test_base_is_clear_of_the_stock_range(self):
        """Stock SPAs occupy 5000-5009; clones must not collide."""
        assert CLONE_PORT_BASE > 5009


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscoverSpas:
    def test_parses_name_and_port(self):
        out = "auth=5000\nmentor=5001\n"
        with patch("iblai_infra.ansible.runner.AnsibleRunner.run_remote_script", return_value=out):
            assert discover_spas(_state()) == {"auth": 5000, "mentor": 5001}

    def test_missing_port_becomes_zero_not_a_crash(self):
        with patch("iblai_infra.ansible.runner.AnsibleRunner.run_remote_script", return_value="broken=\n"):
            assert discover_spas(_state()) == {"broken": 0}

    def test_unreachable_returns_none(self):
        with patch("iblai_infra.ansible.runner.AnsibleRunner.run_remote_script", return_value=None):
            assert discover_spas(_state()) is None


# ---------------------------------------------------------------------------
# clone
# ---------------------------------------------------------------------------


class TestClone:
    def test_passes_everything_the_role_needs(self, applied):
        spa_clone(name="acme", source="mentor", new_name="mentor-custom",
                  domain="custom.acme.example.com", port=None)
        assert applied.tags == CLONE_TAGS
        e = applied.extra
        assert e["spa_clone_enabled"] is True
        assert e["spa_clone_source"] == "mentor"
        assert e["spa_clone_source_port"] == 5001   # needed to rewrite the port mapping
        assert e["spa_clone_name"] == "mentor-custom"
        assert e["spa_clone_port"] == CLONE_PORT_BASE
        assert e["spa_clone_domain"] == "custom.acme.example.com"

    def test_unknown_source_is_rejected(self, applied):
        with pytest.raises(typer.Exit):
            spa_clone(name="acme", source="nope", new_name="x",
                      domain="x.example.com", port=None)

    def test_existing_name_is_rejected(self, applied):
        with pytest.raises(typer.Exit):
            spa_clone(name="acme", source="mentor", new_name="os",
                      domain="x.example.com", port=None)

    def test_port_already_in_use_is_rejected(self, applied):
        with pytest.raises(typer.Exit):
            spa_clone(name="acme", source="mentor", new_name="x",
                      domain="x.example.com", port=5001)

    @pytest.mark.parametrize("bad", ["Has Space", "-lead", "semi;colon", "with/slash"])
    def test_invalid_names_are_rejected(self, applied, bad):
        with pytest.raises(typer.Exit):
            spa_clone(name="acme", source="mentor", new_name=bad,
                      domain="x.example.com", port=None)

    def test_uppercase_is_normalised_not_rejected(self, applied):
        """Matches `platform create`; the name becomes a directory, so lowercase."""
        spa_clone(name="acme", source="mentor", new_name="Mentor-Custom",
                  domain="x.example.com", port=None)
        assert applied.extra["spa_clone_name"] == "mentor-custom"

    @pytest.mark.parametrize(
        "given,expected",
        [
            ("https://Custom.Example.com/", "custom.example.com"),
            ("http://custom.example.com", "custom.example.com"),
            ("Custom.Example.com", "custom.example.com"),
        ],
    )
    def test_domain_is_normalised(self, applied, given, expected):
        spa_clone(name="acme", source="mentor", new_name="c", domain=given, port=None)
        assert applied.extra["spa_clone_domain"] == expected

    def test_explicit_port_is_honoured(self, applied):
        spa_clone(name="acme", source="mentor", new_name="c",
                  domain="c.example.com", port=5099)
        assert applied.extra["spa_clone_port"] == 5099

    def test_unreachable_server_fails_cleanly(self, monkeypatch):
        monkeypatch.setattr("iblai_infra.terraform.state.load_state", lambda name: _state())
        monkeypatch.setattr("iblai_infra.features.spa.discover_spas", lambda st: None)
        with pytest.raises(typer.Exit):
            spa_clone(name="acme", source="mentor", new_name="c",
                      domain="c.example.com", port=None)


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


class TestRemove:
    @pytest.mark.parametrize("stock", sorted(STOCK_SPAS))
    def test_stock_spas_cannot_be_removed(self, applied, stock):
        """This role deletes a directory and reloads nginx — never at a stock SPA."""
        with pytest.raises(typer.Exit):
            spa_remove(name="acme", spa=stock, assume_yes=True)
        assert applied.tags is None  # nothing ran

    def test_a_clone_can_be_removed(self, applied):
        spa_remove(name="acme", spa="mentor-custom", assume_yes=True)
        assert applied.tags == REMOVE_TAGS
        assert applied.extra["spa_remove_name"] == "mentor-custom"
        assert applied.extra["spa_remove_enabled"] is True

    def test_name_is_normalised_before_the_stock_check(self, applied):
        """'MENTOR' must not slip past the guard on case."""
        with pytest.raises(typer.Exit):
            spa_remove(name="acme", spa="MENTOR", assume_yes=True)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


class TestWiring:
    def test_clone_and_remove_use_distinct_tags(self):
        assert CLONE_TAGS == ["spa_clone"]
        assert REMOVE_TAGS == ["spa_remove"]

    def test_playbook_carries_both_roles(self):
        import yaml

        playbook = (
            Path(__file__).resolve().parents[2]
            / "src/iblai_infra/ansible/templates/single-server/playbook.yml"
        )
        roles = yaml.safe_load(playbook.read_text())[0]["roles"]
        tagged = {r["role"]: r["tags"] for r in roles if isinstance(r, dict) and r.get("tags")}
        assert tagged["spa_clone"] == ["spa_clone"]
        assert tagged["spa_clone_remove"] == ["spa_remove"]

    def test_extra_vars_reach_the_ansible_command(self):
        """The role reads these as ansible vars; they must land in --extra-vars."""
        from iblai_infra.ansible.runner import AnsibleRunner

        runner = AnsibleRunner(_state(), SetupConfig.for_feature(_state()))
        runner.extra_vars = {"spa_clone_name": "mentor-custom", "spa_clone_port": 5060}
        built = runner._build_extra_vars()
        assert built["spa_clone_name"] == "mentor-custom"
        assert built["spa_clone_port"] == 5060
        # and the standard vars survive alongside them
        assert "base_domain" in built

    def test_extra_vars_cleared_after_a_partial_run(self):
        from iblai_infra.ansible.runner import AnsibleRunner

        runner = AnsibleRunner(_state(), SetupConfig.for_feature(_state()))
        with patch.object(runner, "_run_ansible", return_value=(True, 1)), \
             patch.object(runner, "_print_final_table"):
            runner.run_partial(["spa_clone"], extra_vars={"spa_clone_name": "x"})
        assert runner.extra_vars is None
