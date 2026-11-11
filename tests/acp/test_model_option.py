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


def _add_model(config_dir: Path, alias: str) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["models"].append({
        "name": "mistral-small-latest",
        "provider": "mistral",
        "alias": alias,
    })
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


@pytest.mark.asyncio
async def test_the_model_option_picks_through_the_app_server(
    config_dir: Path, tmp_path: Path
) -> None:
    """*Prepare*: An ACP session over a real backend with two models.
    *Do*: Pick the other model, then a model the configuration does not offer.
    *Assert*: The app-server takes the first pick and refuses the second; the
    session stays on the model it picked.
    """
    _add_model(config_dir, "small")
    agent = _connected_agent()
    response = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])

    try:
        await agent.set_config_option(
            config_id="model", session_id=response.session_id, value="small"
        )
        with pytest.raises(InvalidRequestError):
            await agent.set_config_option(
                config_id="model", session_id=response.session_id, value="nope"
            )
        session = agent.sessions[response.session_id]
        active = session.app_server.resources.config.current.active_model.alias
    finally:
        await agent.close()

    assert active == "small"
