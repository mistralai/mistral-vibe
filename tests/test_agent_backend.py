from __future__ import annotations

from unittest.mock import MagicMock

from mcp.types import (
    CreateMessageRequestParams,
    CreateMessageResult,
    SamplingMessage,
    TextContent,
)
import pytest

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_config,
    make_test_models,
)
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfig
from vibe.core.tools.base import BaseToolConfig, ToolPermission
from vibe.core.types import Backend, EntrypointMetadata, FunctionCall, ToolCall


def _two_model_vibe_config(active_model: str) -> VibeConfig:
    """VibeConfig with two models so we can switch active_model."""
    models = [
        ModelConfig(
            name="mistral-vibe-cli-latest", provider="mistral", alias="devstral-latest"
        ),
        ModelConfig(
            name="devstral-small-latest", provider="mistral", alias="devstral-small"
        ),
    ]
    providers = [
        ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
            backend=Backend.MISTRAL,
        )
    ]
    return build_test_vibe_config(
        active_model=active_model, models=models, providers=providers
    )


def _make_sampling_params() -> CreateMessageRequestParams:
    return CreateMessageRequestParams(
        messages=[
            SamplingMessage(role="user", content=TextContent(type="text", text="Hi"))
        ],
        systemPrompt=None,
        temperature=None,
        maxTokens=100,
    )


@pytest.mark.asyncio
async def test_passes_x_affinity_header_when_asking_an_answer(vibe_config: VibeConfig):
    backend = FakeBackend([mock_llm_chunk(content="Response")])
    agent = build_test_agent_loop(config=vibe_config, backend=backend)

    [_ async for _ in agent.act("Hello")]

    assert len(backend.requests_extra_headers) > 0
    headers = backend.requests_extra_headers[0]
    assert headers is not None
    assert "x-affinity" in headers
    assert headers["x-affinity"] == agent.session_id


@pytest.mark.asyncio
async def test_passes_x_affinity_header_when_asking_an_answer_streaming(
    vibe_config: VibeConfig,
):
    backend = FakeBackend([mock_llm_chunk(content="Response")])
    agent = build_test_agent_loop(
        config=vibe_config, backend=backend, enable_streaming=True
    )

    [_ async for _ in agent.act("Hello")]

    assert len(backend.requests_extra_headers) > 0
    headers = backend.requests_extra_headers[0]
    assert headers is not None
    assert "x-affinity" in headers
    assert headers["x-affinity"] == agent.session_id


@pytest.mark.asyncio
async def test_updates_tokens_stats_based_on_backend_response(vibe_config: VibeConfig):
    chunk = mock_llm_chunk(content="Response", prompt_tokens=100, completion_tokens=50)
    backend = FakeBackend([chunk])
    agent = build_test_agent_loop(config=vibe_config, backend=backend)

    [_ async for _ in agent.act("Hello")]

    assert agent.stats.context_tokens == 150


@pytest.mark.asyncio
async def test_updates_tokens_stats_based_on_backend_response_streaming(
    vibe_config: VibeConfig,
):
    final_chunk = mock_llm_chunk(
        content="Complete", prompt_tokens=200, completion_tokens=75
    )
    backend = FakeBackend([final_chunk])
    agent = build_test_agent_loop(
        config=vibe_config, backend=backend, enable_streaming=True
    )

    [_ async for _ in agent.act("Hello")]

    assert agent.stats.context_tokens == 275


@pytest.mark.asyncio
async def test_passes_session_id_to_backend(vibe_config: VibeConfig):
    backend = FakeBackend([mock_llm_chunk(content="Response")])
    agent = build_test_agent_loop(config=vibe_config, backend=backend)

    [_ async for _ in agent.act("Hello")]

    assert len(backend.requests_metadata) > 0
    meta = backend.requests_metadata[0]
    assert meta is not None
    assert meta["session_id"] == agent.session_id
    assert "message_id" in meta
    assert meta["is_user_prompt"] == "true"
    assert meta["call_type"] == "main_call"
    assert meta["call_source"] == "vibe_code"


@pytest.mark.asyncio
async def test_passes_entrypoint_metadata_to_backend(vibe_config: VibeConfig):
    metadata = EntrypointMetadata(
        agent_entrypoint="acp",
        agent_version="2.0.0",
        client_name="vibe_ide",
        client_version="0.5.0",
    )
    backend = FakeBackend([mock_llm_chunk(content="Response")])
    agent = build_test_agent_loop(
        config=vibe_config,
        backend=backend,
        enable_streaming=True,
        entrypoint_metadata=metadata,
    )

    [_ async for _ in agent.act("Hello")]

    assert len(backend.requests_metadata) > 0
    meta = backend.requests_metadata[0]
    assert meta is not None
    assert meta["agent_entrypoint"] == "acp"
    assert meta["agent_version"] == "2.0.0"
    assert meta["client_name"] == "vibe_ide"
    assert meta["client_version"] == "0.5.0"
    assert meta["session_id"] == agent.session_id
    assert "message_id" in meta
    assert meta["is_user_prompt"] == "true"
    assert meta["call_type"] == "main_call"
    assert meta["call_source"] == "vibe_code"


@pytest.mark.asyncio
async def test_mcp_sampling_handler_uses_updated_backend_when_agent_backend_changes():
    """AgentLoop's MCP sampling handler uses current backend when backend is reassigned."""
    backend1 = FakeBackend([mock_llm_chunk(content="from-backend-1")])
    backend2 = FakeBackend([mock_llm_chunk(content="from-backend-2")])
    config = _two_model_vibe_config("devstral-latest")
    agent = build_test_agent_loop(config=config, backend=backend1)
    handler = agent._sampling_handler
    params = _make_sampling_params()
    context = MagicMock()

    result1 = await handler(context, params)
    assert isinstance(result1, CreateMessageResult)
    assert result1.content.type == "text"
    assert result1.content.text == "from-backend-1"
    assert len(backend1.requests_messages) == 1
    assert len(backend2.requests_messages) == 0

    agent.backend = backend2
    result2 = await handler(context, params)
    assert isinstance(result2, CreateMessageResult)
    assert result2.content.type == "text"
    assert result2.content.text == "from-backend-2"
    assert len(backend1.requests_messages) == 1
    assert len(backend2.requests_messages) == 1


@pytest.mark.asyncio
async def test_mcp_sampling_handler_uses_updated_config_when_agent_config_changes():
    chunk = mock_llm_chunk(content="ok")
    backend = FakeBackend([chunk])
    config1 = _two_model_vibe_config("devstral-latest")
    config2 = _two_model_vibe_config("devstral-small")
    agent = build_test_agent_loop(config=config1, backend=backend)
    handler = agent._sampling_handler
    params = _make_sampling_params()
    context = MagicMock()

    result1 = await handler(context, params)
    assert isinstance(result1, CreateMessageResult)
    assert result1.model == "mistral-vibe-cli-latest"

    agent._base_config = config2
    agent.agent_manager.invalidate_config()
    result2 = await handler(context, params)
    assert isinstance(result2, CreateMessageResult)
    assert result2.model == "devstral-small-latest"


def _generic_provider_vibe_config() -> VibeConfig:
    """VibeConfig with generic backend so no metadata header is sent."""
    providers = [
        ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
            backend=Backend.GENERIC,
        )
    ]
    return build_test_vibe_config(providers=providers)


@pytest.mark.asyncio
async def test_mistral_metadata_header_is_user_prompt_per_turn() -> None:
    """First LLM call in a turn has is_user_prompt=True; second call (after tools) has is_user_prompt=False."""
    tool_call = ToolCall(
        id="call_1",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action": "read"}'),
    )
    backend = FakeBackend([
        [mock_llm_chunk(content="Checking todos.", tool_calls=[tool_call])],
        [mock_llm_chunk(content="Here are your todos.")],
    ])
    config = build_test_vibe_config(
        providers=[
            ProviderConfig(
                name="mistral",
                api_base="https://api.mistral.ai/v1",
                api_key_env_var="MISTRAL_API_KEY",
                backend=Backend.MISTRAL,
            )
        ],
        enabled_tools=["todo"],
        tools={"todo": BaseToolConfig(permission=ToolPermission.ALWAYS)},
    )
    agent = build_test_agent_loop(
        config=config, backend=backend, agent_name=BuiltinAgentName.AUTO_APPROVE
    )

    [_ async for _ in agent.act("What's on my todo list?")]

    assert len(backend.requests_metadata) == 2
    first_metadata = backend.requests_metadata[0]
    second_metadata = backend.requests_metadata[1]
    assert first_metadata is not None and "is_user_prompt" in first_metadata
    assert second_metadata is not None and "is_user_prompt" in second_metadata
    assert first_metadata["is_user_prompt"] == "true"
    assert second_metadata["is_user_prompt"] == "false"
    assert first_metadata["call_type"] == "main_call"
    assert second_metadata["call_type"] == "secondary_call"


@pytest.mark.asyncio
async def test_auto_compact_internal_chat_has_is_user_prompt_false_then_user_turn_true() -> (
    None
):
    """Compact's internal _chat() sends is_user_prompt=False; the following user turn sends is_user_prompt=True."""
    backend = FakeBackend([
        [mock_llm_chunk(content="<summary>")],
        [mock_llm_chunk(content="<final>")],
    ])
    config = build_test_vibe_config(
        models=make_test_models(auto_compact_threshold=1),
        providers=[
            ProviderConfig(
                name="mistral",
                api_base="https://api.mistral.ai/v1",
                api_key_env_var="MISTRAL_API_KEY",
                backend=Backend.MISTRAL,
            )
        ],
    )
    agent = build_test_agent_loop(config=config, backend=backend)
    agent.stats.context_tokens = 2

    [_ async for _ in agent.act("Hello")]

    assert len(backend.requests_metadata) == 2
    compact_metadata = backend.requests_metadata[0]
    user_turn_metadata = backend.requests_metadata[1]
    assert compact_metadata is not None and "is_user_prompt" in compact_metadata
    assert user_turn_metadata is not None and "is_user_prompt" in user_turn_metadata
    assert compact_metadata["is_user_prompt"] == "false"
    assert user_turn_metadata["is_user_prompt"] == "true"
    assert compact_metadata["call_type"] == "secondary_call"
    assert user_turn_metadata["call_type"] == "main_call"


@pytest.mark.asyncio
async def test_generic_provider_has_no_metadata_header() -> None:
    """Non-Mistral provider does not send the metadata header."""
    backend = FakeBackend([mock_llm_chunk(content="Response")])
    config = _generic_provider_vibe_config()
    agent = build_test_agent_loop(config=config, backend=backend)

    [_ async for _ in agent.act("Hello")]

    assert len(backend.requests_extra_headers) == 1
    headers = backend.requests_extra_headers[0]
    assert headers is not None
    assert "metadata" not in headers
