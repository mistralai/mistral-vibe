from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from vibe.acp import agent as agent_module


@pytest.mark.asyncio
async def test_acp_agent_closes_when_protocol_stream_reaches_eof(monkeypatch) -> None:
    run_agent = AsyncMock()
    close = AsyncMock()
    agent: Any = SimpleNamespace(close=close)
    monkeypatch.setattr(agent_module, "run_agent", run_agent)

    await agent_module._serve_acp_agent(agent)

    run_agent.assert_awaited_once()
    close.assert_awaited_once()
