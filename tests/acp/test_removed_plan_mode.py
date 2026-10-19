from __future__ import annotations

from pathlib import Path
import tomllib

import pytest
import tomli_w

from tests.stubs.fake_client import FakeClient
from vibe.acp.agent import VibeAcpAgent
from vibe.acp.exceptions import InvalidRequestError


def _connected_agent() -> VibeAcpAgent:
    agent = VibeAcpAgent()
    client = FakeClient()
    agent.on_connect(client)
    client.on_connect(agent)
    return agent


@pytest.mark.asyncio
async def test_a_client_requesting_plan_mode_keeps_the_current_mode(
    tmp_path: Path,
) -> None:
    """*Prepare*: An ACP session over a real backend.
    *Do*: Ask for the removed ``plan`` mode through both mode setters.
    *Assert*: ``plan`` is not offered, the mode setter ignores it, the config
    option setter rejects it, and the session stays on its mode.
    """
    agent = _connected_agent()
    response = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    assert response.modes is not None
    current = response.modes.current_mode_id

    try:
        ignored = await agent.set_session_mode(
            session_id=response.session_id, mode_id="plan"
        )
        with pytest.raises(InvalidRequestError):
            await agent.set_config_option(
                config_id="mode", session_id=response.session_id, value="plan"
            )
        session = agent.sessions[response.session_id]
        active = session.app_server.resources.agents.active.name
    finally:
        await agent.close()

    assert "plan" not in [mode.id for mode in response.modes.available_modes]
    assert ignored is None
    assert active == current


@pytest.mark.asyncio
async def test_a_config_defaulting_to_plan_opens_sessions_on_ask(
    config_dir: Path, tmp_path: Path
) -> None:
    """*Prepare*: A user config still naming the removed ``plan`` as default agent.
    *Do*: Open an ACP session.
    *Assert*: The session opens on ``ask`` instead of failing.
    """
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config_file.write_text(
        tomli_w.dumps({**config, "default_agent": "plan"}), encoding="utf-8"
    )
    agent = _connected_agent()

    try:
        response = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    finally:
        await agent.close()

    assert response.modes is not None
    assert response.modes.current_mode_id == "ask"
