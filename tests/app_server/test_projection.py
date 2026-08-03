from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from vibe.app_server._projection import (
    project_config,
    project_history,
    project_session_log,
    project_stats,
)
from vibe.app_server.models import (
    AgentStatsSnapshot,
    CancelledEffectState,
    CompletedEffectState,
    FailedEffectState,
    PublicEffectEntry,
    PublicMessageEntry,
    PublicReasoningEntry,
    ResourceContentBlock,
)
from vibe.core.config import SessionLoggingConfig
from vibe.core.types import (
    FunctionCall,
    ImageAttachment,
    InlineImageSource,
    LLMMessage,
    Role,
    ToolCall,
)
from vibe.core.utils import CANCELLATION_TAG, TOOL_ERROR_TAG
from vibe.user_content import UserDisplayContent, UserResourceLink


def _history_with_tool_output(tool_name: str, output: str):
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(enabled_tools=["task"])
    )
    agent_loop.messages.reset([
        LLMMessage(role=Role.system, content="system"),
        LLMMessage(role=Role.user, content="delegate"),
        LLMMessage(
            role=Role.assistant,
            content="",
            reasoning_content="Thinking",
            reasoning_message_id="reasoning-1",
            tool_calls=[
                ToolCall(
                    id="tool-1",
                    function=FunctionCall(
                        name=tool_name,
                        arguments=json.dumps({"task": "inspect", "agent": "explore"}),
                    ),
                )
            ],
        ),
        LLMMessage(
            role=Role.tool, name=tool_name, tool_call_id="tool-1", content=output
        ),
    ])
    return project_history(agent_loop)


@pytest.mark.parametrize(
    ("tag", "state_type"),
    [(CANCELLATION_TAG, CancelledEffectState), (TOOL_ERROR_TAG, FailedEffectState)],
)
def test_persisted_tagged_tool_result_is_terminal_and_stripped(
    tag: str, state_type: type[CancelledEffectState | FailedEffectState]
) -> None:
    history = _history_with_tool_output("task", f"<{tag}>Stopped</{tag}>")

    reasoning = next(
        entry for entry in history if isinstance(entry, PublicReasoningEntry)
    )
    effect = next(entry for entry in history if isinstance(entry, PublicEffectEntry))
    assert reasoning.generation_status == "completed"
    assert isinstance(effect.state, state_type)
    assert effect.state.output_text == "Stopped"
    assert effect.generation_status == "completed"


def test_persisted_known_tool_output_is_not_treated_as_typed_result() -> None:
    history = _history_with_tool_output(
        "task", "response: done\nturns_used: 1\ncompleted: True"
    )

    effect = next(entry for entry in history if isinstance(entry, PublicEffectEntry))
    assert isinstance(effect.state, CompletedEffectState)
    assert effect.state.output is None
    assert effect.state.output_text.startswith("response: done")


def test_config_view_redacts_persistence_paths() -> None:
    agent_loop = build_test_agent_loop()

    config = project_config(agent_loop)

    assert "sessionLogging" not in config.model_dump(mode="json", by_alias=True)


def test_stats_projection_includes_cached_token_counts() -> None:
    agent_loop = build_test_agent_loop()
    agent_loop.stats.session_cached_tokens = 42
    agent_loop.stats.last_turn_cached_tokens = 7
    agent_loop.stats.input_price_per_million = 1.0
    agent_loop.stats.cached_input_price_per_million = 0.1

    stats = project_stats(agent_loop)
    serialized = stats.model_dump(mode="json", by_alias=True)

    assert stats.session_cached_tokens == 42
    assert stats.last_turn_cached_tokens == 7
    assert stats.cached_input_price_per_million == 0.1
    assert serialized["sessionCachedTokens"] == 42
    assert serialized["lastTurnCachedTokens"] == 7


def test_snapshot_session_cost_discounts_cached_tokens() -> None:
    snapshot = AgentStatsSnapshot(
        session_prompt_tokens=1_000_000,
        session_completion_tokens=0,
        session_cached_tokens=400_000,
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
    )
    # 600k * $1/M + 400k * $0.1/M = $0.64
    assert snapshot.session_cost == pytest.approx(0.64)


def test_snapshot_session_cost_bills_cached_at_input_rate_when_unset() -> None:
    snapshot = AgentStatsSnapshot(
        session_prompt_tokens=1_000_000,
        session_completion_tokens=0,
        session_cached_tokens=400_000,
        input_price_per_million=1.0,
    )
    assert snapshot.session_cost == pytest.approx(1.0)


def test_snapshot_session_cost_never_negative_when_cached_exceeds_prompt() -> None:
    snapshot = AgentStatsSnapshot(
        session_prompt_tokens=100_000,
        session_completion_tokens=0,
        session_cached_tokens=190_000,
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
    )
    assert snapshot.session_cost == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_session_log_is_persisted_only_after_it_is_saved(tmp_path: Path) -> None:
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(
            session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
        )
    )

    fresh = project_session_log(agent_loop)
    await agent_loop.persist_empty_session()
    saved = project_session_log(agent_loop)
    agent_loop.session_logger.reset_session("replacement")
    replacement = project_session_log(agent_loop)

    assert fresh.persisted is False
    assert fresh.path is None
    assert saved.persisted is True
    assert saved.path is not None
    assert replacement.persisted is False
    assert replacement.path is None


def test_persisted_user_message_preserves_images_and_display_metadata() -> None:
    agent_loop = build_test_agent_loop()
    metadata = UserDisplayContent(
        version="1.0.0",
        host="mistral-vscode",
        content=[{"type": "workspace_mention", "name": "app.py"}],
    )
    agent_loop.messages.reset([
        LLMMessage(
            role=Role.user,
            content="Look at app.py",
            message_id="user-1",
            images=[
                ImageAttachment(
                    source=InlineImageSource(data="aW1hZ2U="),
                    alias="diagram.png",
                    mime_type="image/png",
                )
            ],
            user_display_content=metadata,
        )
    ])

    entry = project_history(agent_loop)[0]

    assert isinstance(entry, PublicMessageEntry)
    assert [image.alias for image in entry.images] == ["diagram.png"]
    assert entry.user_display_content == metadata


def test_persisted_user_message_preserves_structured_resources() -> None:
    agent_loop = build_test_agent_loop()
    agent_loop.messages.reset([
        LLMMessage(
            role=Role.user,
            content=(
                "Review the reference\n\n"
                "Resource: Specification\nURI: file:///workspace/spec.md"
            ),
            input_text="Review the reference",
            resources=[
                UserResourceLink(
                    uri="file:///workspace/spec.md",
                    media_type="text/markdown",
                    title="Specification",
                )
            ],
        )
    ])

    entry = project_history(agent_loop)[0]

    assert isinstance(entry, PublicMessageEntry)
    assert entry.text == "Review the reference"
    resource = next(
        block for block in entry.content if isinstance(block, ResourceContentBlock)
    )
    assert resource.resource.uri == "file:///workspace/spec.md"
    assert resource.resource.media_type == "text/markdown"
