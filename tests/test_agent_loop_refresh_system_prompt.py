from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config


@pytest.mark.asyncio
async def test_refresh_system_prompt_preserves_scratchpad_section() -> None:
    # Regression: refresh_system_prompt must pass scratchpad_dir, otherwise
    # it silently drops the scratchpad instructions from the system prompt.
    # This fires on every session start/resume via initialize_experiments
    # and hydrate_experiments_from_session, so the LLM would lose awareness
    # of the scratchpad on the very first turn for any user with telemetry
    # enabled and a Mistral API key.
    config = build_test_vibe_config(
        include_project_context=False,
        include_prompt_detail=True,
        include_model_info=False,
        include_commit_signature=False,
    )
    agent = build_test_agent_loop(config=config)

    initial_prompt = agent.messages[0].content or ""
    assert "Scratchpad Directory" in initial_prompt
    assert agent.scratchpad_dir is not None
    assert str(agent.scratchpad_dir) in initial_prompt

    await agent.refresh_system_prompt()

    refreshed_prompt = agent.messages[0].content or ""
    assert "Scratchpad Directory" in refreshed_prompt
    assert str(agent.scratchpad_dir) in refreshed_prompt
