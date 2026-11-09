from __future__ import annotations

from pathlib import Path

import pytest

from tests.stubs.fake_client import FakeClient
from vibe.acp.agent import VibeAcpAgent
from vibe.acp.exceptions import InvalidRequestError


@pytest.mark.asyncio
async def test_review_mutation_rejects_invalid_target(tmp_path: Path) -> None:
    agent = VibeAcpAgent()
    client = FakeClient()
    agent.on_connect(client)
    client.on_connect(agent)
    response = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])

    try:
        with pytest.raises(InvalidRequestError):
            await agent.ext_method(
                "review/approve",
                {"sessionId": response.session_id, "target": {"kind": "bogus"}},
            )
    finally:
        await agent.close()
