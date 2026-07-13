from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from vibe.core.agent_loop import AgentLoop


@pytest.fixture
def agent_loop_with_context_summary() -> AgentLoop:
    config = build_test_vibe_config(experimental_teleport_context_summary=True)
    return build_test_agent_loop(config=config)
