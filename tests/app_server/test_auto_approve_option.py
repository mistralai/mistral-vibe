from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import ConfigBuilder, OrchestratorLoader
from vibe.app_server._auto_approve_option import resolve_start_auto_approve
from vibe.app_server.protocol import SessionOptions
from vibe.core.agents.manager import AgentManager
from vibe.core.config import VibeConfigSchema


@pytest.fixture
def agents_for(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
):
    def build(agent: str | None = None, **config: object) -> AgentManager:
        return AgentManager(load_orchestrator(build_config(**config)), agent)

    return build


def test_without_auto_approve_the_options_are_unchanged(agents_for) -> None:
    options = SessionOptions(approval_policy="ask")

    assert resolve_start_auto_approve(options, agents_for()) is options


@pytest.mark.parametrize("policy", ["ask", "accept-edits", "smart-approve"])
def test_a_builtin_policy_becomes_the_auto_approve_policy(
    agents_for, policy: str
) -> None:
    options = SessionOptions(approval_policy=policy, auto_approve=True)

    resolved = resolve_start_auto_approve(options, agents_for())

    assert resolved.approval_policy == "auto-approve"
    assert resolved.auto_approve is False


def test_a_custom_agent_keeps_its_agent_and_takes_the_auto_approve_policy(
    agents_for, tmp_path: Path
) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "reviewer.toml").write_text('description = "Reviews"\n')
    agents = agents_for("reviewer", agent_paths=[str(agents_dir)])
    options = SessionOptions(agent="reviewer", auto_approve=True)

    resolved = resolve_start_auto_approve(options, agents)

    assert resolved.agent == "reviewer"
    assert resolved.approval_policy == "auto-approve"
    assert resolved.auto_approve is False


def test_a_disabled_auto_approve_mode_keeps_the_clamped_bypass(agents_for) -> None:
    options = SessionOptions(auto_approve=True)

    resolved = resolve_start_auto_approve(
        options, agents_for(enable_auto_approve=False)
    )

    assert resolved is options


def test_an_auto_approve_override_that_asks_is_not_the_target(
    agents_for, tmp_path: Path
) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "auto-approve.toml").write_text('description = "Asks"\n')
    agents = agents_for(agent_paths=[str(agents_dir)])
    options = SessionOptions(auto_approve=True)

    assert resolve_start_auto_approve(options, agents) is options
