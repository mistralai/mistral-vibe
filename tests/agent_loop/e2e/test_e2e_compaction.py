from __future__ import annotations

import pytest

from tests.agent_loop.e2e.conftest import MistralAPI, build_e2e_agent_loop
from tests.backend.data.mistral import mistral_completion
from tests.conftest import build_test_vibe_config, make_test_models
from vibe.core.types import CompactStartEvent

COMPACTION_MODELS = make_test_models(auto_compact_threshold=1)


@pytest.mark.asyncio
async def test_auto_compaction_preserves_user_message_and_embeds_summary(
    mistral_api: MistralAPI,
) -> None:
    mistral_api.reply(
        # First: the compaction summary
        mistral_completion("<summary>A summary of what happened so far</summary>"),
        # Then: the final answer to user message
        mistral_completion("final answer"),
    )
    agent = build_e2e_agent_loop(
        config=build_test_vibe_config(models=COMPACTION_MODELS)
    )
    agent.stats.context_tokens = 5  # Trigger the auto-compaction immediately

    events = [event async for event in agent.act("Investigate the bug")]

    assert any(isinstance(e, CompactStartEvent) for e in events)
    sent_after_compaction = mistral_api.model_facing_text(1)
    assert "Investigate the bug" in sent_after_compaction
    assert "A summary of what happened so far" in sent_after_compaction


@pytest.mark.asyncio
async def test_repeated_auto_compaction_preserves_earlier_user_messages(
    mistral_api: MistralAPI,
) -> None:
    mistral_api.reply(
        mistral_completion("<summary>summary one</summary>"),
        mistral_completion("reply one"),
        mistral_completion("<summary>summary two</summary>"),
        mistral_completion("reply two"),
    )
    agent = build_e2e_agent_loop(
        config=build_test_vibe_config(models=COMPACTION_MODELS)
    )

    agent.stats.context_tokens = 5
    [_ async for _ in agent.act("first ask")]
    agent.stats.context_tokens = 5
    [_ async for _ in agent.act("second ask")]

    sent_after_second_compaction = mistral_api.model_facing_text(3)
    assert "first ask" in sent_after_second_compaction
    assert "second ask" in sent_after_second_compaction


@pytest.mark.asyncio
async def test_oversized_user_message_is_middle_truncated_in_compaction(
    mistral_api: MistralAPI,
) -> None:
    huge_message = "alpha " * 20_000
    mistral_api.reply(
        mistral_completion("<summary>summary intro</summary>"),
        mistral_completion("reply intro"),
        mistral_completion("<summary>summary huge</summary>"),
        mistral_completion("reply huge"),
    )
    agent = build_e2e_agent_loop(
        config=build_test_vibe_config(models=COMPACTION_MODELS)
    )

    agent.stats.context_tokens = 5
    [_ async for _ in agent.act("intro")]
    agent.stats.context_tokens = 5
    [_ async for _ in agent.act(huge_message)]

    sent_after_second_compaction = mistral_api.model_facing_text(3)
    assert "[... truncated ...]" in sent_after_second_compaction
    assert "intro" not in sent_after_second_compaction
