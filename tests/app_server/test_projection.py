from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.fake_connector_registry import FakeConnectorRegistry
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.app_server._projection import (
    project_config,
    project_history,
    project_mcp,
    project_session_log,
    project_stats,
)
from vibe.app_server.models import (
    AgentStatsSnapshot,
    CancelledEffectState,
    CompletedEffectState,
    FailedEffectState,
    MCPSourceStatus,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicMessageEntry,
    PublicReasoningEntry,
    ResourceContentBlock,
)
from vibe.core.config import ConnectorConfig, MCPStdio, SessionLoggingConfig
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


def test_config_view_flags_pinned_active_model() -> None:
    from vibe.core.config import ModelConfig

    models = [
        ModelConfig(name="model-a", provider="mistral", alias="alpha"),
        ModelConfig(name="model-b", provider="mistral", alias="beta"),
    ]
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(models=models, active_model="beta")
    )

    config = project_config(agent_loop)

    assert config.active_model_pinned is True
    assert config.active_model.alias == "beta"


def test_config_view_reports_unpinned_default_model() -> None:
    from vibe.core.config import ModelConfig

    models = [
        ModelConfig(name="model-a", provider="mistral", alias="alpha"),
        ModelConfig(name="model-b", provider="mistral", alias="beta"),
    ]
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(models=models, active_model="")
    )

    config = project_config(agent_loop)

    assert config.active_model_pinned is False
    # The unpinned state resolves to the first configured model for display.
    assert config.default_model_alias == "alpha"
    assert config.active_model.alias == "alpha"


def test_config_view_hydrates_display_name_from_alias() -> None:
    from vibe.core.config import ModelConfig

    models = [
        ModelConfig(name="model-a", provider="mistral", alias="alpha"),
        ModelConfig(
            name="zai-glm-5-2",
            provider="mistral",
            alias="glm-5-2",
            display_name="glm-5.2 (Mistral Hosted)",
        ),
    ]
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(models=models, active_model="alpha")
    )

    config = project_config(agent_loop)

    # Models without a configured display name fall back to their alias, so the
    # UI only ever reads ``display_name``.
    assert [model.display_name for model in config.models] == [
        "alpha",
        "glm-5.2 (Mistral Hosted)",
    ]
    assert config.model_display_name("glm-5-2") == "glm-5.2 (Mistral Hosted)"
    assert config.default_model_display_name == "alpha"
    # An alias that names no configured model is echoed back unchanged.
    assert config.model_display_name("unknown") == "unknown"


def test_config_view_awaiting_experiment_model_on_cold_cache() -> None:
    # Cold-cache first launch: the routed model isn't known yet, so the banner
    # spins instead of naming the model.
    agent_loop = build_test_agent_loop(await_experiment_model=True)

    config = project_config(agent_loop)

    assert config.awaiting_experiment_model is True


def test_config_view_not_awaiting_experiment_model_on_warm_cache() -> None:
    agent_loop = build_test_agent_loop()

    config = project_config(agent_loop)

    assert config.awaiting_experiment_model is False


@pytest.mark.asyncio
async def test_apply_cached_experiment_variants_sets_routed_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # End-to-end for the warm-cache startup path.
    from vibe.app_server._runtime import _apply_cached_experiment_variants
    from vibe.core.config import build_default_orchestrator
    from vibe.core.experiments import cache
    from vibe.core.experiments.active import ExperimentName
    from vibe.core.experiments.models import EvalResponse

    monkeypatch.setattr(cache, "_cache_key", lambda _config: "user-abc")
    orchestrator = await build_default_orchestrator()
    routing = ExperimentName.CLI_MODEL_ROUTING.value
    response = EvalResponse.model_validate({
        "features": {
            routing: {"rules": [{"force": '{"active_model": "magistral-medium"}'}]}
        }
    })
    cache.store_cached_eval_response(orchestrator.config, response)
    assert not orchestrator.config.routed_default_model

    await _apply_cached_experiment_variants(orchestrator)

    assert orchestrator.config.routed_default_model == "magistral-medium"


@pytest.mark.asyncio
async def test_apply_cached_experiment_variants_noop_on_cold_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vibe.app_server._runtime import _apply_cached_experiment_variants
    from vibe.core.config import build_default_orchestrator
    from vibe.core.experiments import cache

    monkeypatch.setattr(cache, "_cache_key", lambda _config: "user-abc")
    orchestrator = await build_default_orchestrator()

    await _apply_cached_experiment_variants(orchestrator)

    assert not orchestrator.config.routed_default_model


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


def test_project_mcp_discovery_error_marks_server_unavailable() -> None:
    server = MCPStdio(name="broken", transport="stdio", command="fake-cmd")
    config = build_test_vibe_config(mcp_servers=[server])
    registry = FakeMCPRegistry()
    registry.sync_active_servers([server])
    agent_loop = build_test_agent_loop(config=config, mcp_registry=registry)

    state = project_mcp(agent_loop, discovery_errors={"broken": "no binary found"})

    (source,) = [s for s in state.sources if s.name == "broken"]
    assert source.status is MCPSourceStatus.UNAVAILABLE


def test_project_mcp_discovery_error_does_not_affect_healthy_server() -> None:
    healthy = MCPStdio(name="healthy", transport="stdio", command="fake-cmd")
    broken = MCPStdio(name="broken", transport="stdio", command="missing-cmd")
    config = build_test_vibe_config(mcp_servers=[healthy, broken])
    registry = FakeMCPRegistry()
    registry.sync_active_servers([healthy, broken])
    agent_loop = build_test_agent_loop(config=config, mcp_registry=registry)

    state = project_mcp(agent_loop, discovery_errors={"broken": "no binary found"})

    statuses = {s.name: s.status for s in state.sources}
    assert statuses["broken"] is MCPSourceStatus.UNAVAILABLE
    assert statuses["healthy"] is not MCPSourceStatus.UNAVAILABLE


def test_persisted_compaction_boundary_projects_completed_checkpoint() -> None:
    agent_loop = build_test_agent_loop()
    agent_loop.messages.reset([
        LLMMessage(role=Role.user, content="Before", message_id="user-1"),
        LLMMessage(
            role=Role.user,
            content="Compacted context",
            injected=True,
            message_id="compaction-1",
            context_boundary="compaction",
        ),
        LLMMessage(role=Role.assistant, content="After", message_id="assistant-1"),
    ])

    history = project_history(agent_loop)

    checkpoint = next(
        entry for entry in history if isinstance(entry, PublicCheckpointEntry)
    )
    assert checkpoint.id == "checkpoint:compaction:compaction-1"
    assert checkpoint.kind == "compaction"
    assert checkpoint.message == "Context compacted"
    assert checkpoint.generation_status == "completed"


def test_project_mcp_surfaces_connector_bootstrap_error() -> None:
    agent_loop = build_test_agent_loop()
    registry = FakeConnectorRegistry(
        bootstrap_error="Failed to load workspace connectors (HTTP 502)."
    )
    agent_loop.connector_registry = registry

    state = project_mcp(agent_loop)

    assert state.connector_error == "Failed to load workspace connectors (HTTP 502)."


def test_project_mcp_no_connector_error_when_healthy() -> None:
    agent_loop = build_test_agent_loop()
    agent_loop.connector_registry = FakeConnectorRegistry()

    state = project_mcp(agent_loop)

    assert state.connector_error is None


def test_project_mcp_surfaces_per_connector_error() -> None:
    config = build_test_vibe_config(
        connectors=[ConnectorConfig(name="slack", disabled=False)]
    )
    agent_loop = build_test_agent_loop(config=config)
    registry = FakeConnectorRegistry(
        connectors={"slack": []},
        connector_errors={"slack": "Slack OAuth token expired"},
    )
    agent_loop.connector_registry = registry

    state = project_mcp(agent_loop)

    slack = next(source for source in state.sources if source.name == "slack")
    assert slack.error == "Slack OAuth token expired"
