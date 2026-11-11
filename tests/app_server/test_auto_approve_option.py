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
    options = SessionOptions(agent="ask")

    assert resolve_start_auto_approve(options, agents_for("ask")) is options


@pytest.mark.parametrize("default_agent", ["ask", "accept-edits", "smart-approve"])
def test_a_builtin_default_becomes_the_auto_approve_agent(
    agents_for, default_agent: str
) -> None:
    agents = agents_for(default_agent=default_agent)

    resolved = resolve_start_auto_approve(SessionOptions(auto_approve=True), agents)

    assert resolved.agent == "auto-approve"
    assert resolved.auto_approve is False


@pytest.mark.parametrize("agent", ["ask", "accept-edits", "smart-approve"])
def test_a_named_builtin_permission_mode_becomes_the_auto_approve_agent(
    agents_for, agent: str
) -> None:
    options = SessionOptions(agent=agent, auto_approve=True)

    resolved = resolve_start_auto_approve(options, agents_for(agent))

    assert resolved.agent == "auto-approve"
    assert resolved.auto_approve is False


@pytest.mark.parametrize("agent", ["auto-approve", None])
def test_the_auto_approve_agent_needs_no_bypass(agents_for, agent: str | None) -> None:
    agents = agents_for(agent, default_agent="auto-approve")

    resolved = resolve_start_auto_approve(
        SessionOptions(agent=agent, auto_approve=True), agents
    )

    assert resolved.agent == agent
    assert resolved.auto_approve is False


@pytest.mark.parametrize("named", [True, False])
def test_a_custom_agent_keeps_the_bypass(
    agents_for, tmp_path: Path, named: bool
) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "reviewer.toml").write_text('description = "Reviews"\n')
    agents = agents_for(
        "reviewer" if named else None,
        default_agent="ask" if named else "reviewer",
        agent_paths=[str(agents_dir)],
    )
    options = SessionOptions(agent="reviewer" if named else None, auto_approve=True)

    assert agents.active_profile.name == "reviewer"
    assert resolve_start_auto_approve(options, agents) is options


def test_a_disabled_auto_approve_mode_keeps_the_clamped_bypass(agents_for) -> None:
    options = SessionOptions(auto_approve=True)

    resolved = resolve_start_auto_approve(
        options, agents_for(enable_auto_approve=False)
    )

    assert resolved is options


def test_a_start_that_fell_back_from_its_agent_keeps_the_bypass(agents_for) -> None:
    options = SessionOptions(agent="gone", auto_approve=True)

    assert resolve_start_auto_approve(options, agents_for("ask")) is options


@pytest.mark.parametrize("name", ["ask", "auto-approve"])
def test_an_override_of_a_builtin_name_keeps_the_bypass(
    agents_for, tmp_path: Path, name: str
) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / f"{name}.toml").write_text('description = "Mine"\n')
    agents = agents_for(default_agent=name, agent_paths=[str(agents_dir)])
    options = SessionOptions(auto_approve=True)

    assert agents.active_profile.source_path is not None
    assert resolve_start_auto_approve(options, agents) is options


def test_an_auto_approve_override_that_asks_is_not_the_target(
    agents_for, tmp_path: Path
) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "auto-approve.toml").write_text('description = "Asks"\n')
    agents = agents_for(default_agent="ask", agent_paths=[str(agents_dir)])
    options = SessionOptions(auto_approve=True)

    assert resolve_start_auto_approve(options, agents) is options
