from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch

from mistralai.client import Mistral
from mistralai.client.errors import SDKError
from mistralai.client.models import (
    ConversationResponse,
    ConversationUsageInfo,
    MessageOutputEntry,
    TextChunk,
    ToolReferenceChunk,
)
import pytest

from tests.conftest import build_test_vibe_config
from tests.mock.utils import collect_result
from vibe.core.config import ProviderConfig, VibeConfig
from vibe.core.tools.base import BaseToolState, InvokeContext, ToolError
from vibe.core.tools.builtins.websearch import (
    WebSearch,
    WebSearchArgs,
    WebSearchConfig,
    WebSearchResult,
    WebSearchSource,
)
from vibe.core.tools.manager import ToolManager
from vibe.core.types import Backend, ToolResultEvent

if TYPE_CHECKING:
    from vibe.core.agents.manager import AgentManager


class InMemoryAgentManager:
    def __init__(self, config: VibeConfig) -> None:
        self.config = config


def _ctx_with_config(config: VibeConfig) -> InvokeContext:
    return InvokeContext(
        tool_call_id="t1",
        agent_manager=cast("AgentManager", InMemoryAgentManager(config)),
    )


def _mistral_provider(
    api_key_env_var: str = "MISTRAL_API_KEY",
    api_base: str = "https://on-prem.example.com/v1",
) -> ProviderConfig:
    return ProviderConfig(
        name="mistral",
        api_base=api_base,
        api_key_env_var=api_key_env_var,
        backend=Backend.MISTRAL,
    )


def _llamacpp_provider() -> ProviderConfig:
    return ProviderConfig(
        name="llamacpp", api_base="http://127.0.0.1:8080/v1", backend=Backend.GENERIC
    )


def _make_response(
    content: list | None = None, outputs: list | None = None
) -> ConversationResponse:
    if outputs is None:
        outputs = [MessageOutputEntry(content=content or [])]
    return ConversationResponse(
        conversation_id="test",
        outputs=outputs,
        usage=ConversationUsageInfo(
            prompt_tokens=10, completion_tokens=20, total_tokens=30
        ),
    )


@pytest.fixture
def websearch(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    config = WebSearchConfig()
    return WebSearch(config_getter=lambda: config, state=BaseToolState())


def test_parse_text_chunks(websearch):
    response = _make_response(
        content=[TextChunk(text="Hello "), TextChunk(text="world")]
    )
    result = websearch._parse_response(response, "test query")
    assert result.query == "test query"
    assert result.answer == "Hello world"
    assert result.sources == []


def test_parse_plain_string_content(websearch):
    # Short answers come back as a plain string, not a list of chunks.
    response = _make_response(outputs=[MessageOutputEntry(content="2 + 2 = 4.")])
    result = websearch._parse_response(response, "2 plus 2")
    assert result.answer == "2 + 2 = 4."
    assert result.sources == []


def test_parse_sources_deduped(websearch):
    response = _make_response(
        content=[
            TextChunk(text="Answer"),
            ToolReferenceChunk(tool="web_search", title="Site A", url="https://a.com"),
            ToolReferenceChunk(
                tool="web_search", title="Site A duplicate", url="https://a.com"
            ),
            ToolReferenceChunk(tool="web_search", title="Site B", url="https://b.com"),
        ]
    )
    result = websearch._parse_response(response, "test query")
    assert result.answer == "Answer"
    assert len(result.sources) == 2
    assert result.sources[0].url == "https://a.com"
    assert result.sources[0].title == "Site A"
    assert result.sources[1].url == "https://b.com"


def test_parse_skips_source_without_url(websearch):
    response = _make_response(
        content=[
            TextChunk(text="Answer"),
            ToolReferenceChunk(tool="web_search", title="No URL"),
        ]
    )
    result = websearch._parse_response(response, "test query")
    assert result.sources == []


def test_parse_empty_text_raises(websearch):
    response = _make_response(content=[])
    with pytest.raises(ToolError, match="No text in agent response"):
        websearch._parse_response(response, "test query")


def test_parse_whitespace_only_raises(websearch):
    response = _make_response(content=[TextChunk(text="   ")])
    with pytest.raises(ToolError, match="No text in agent response"):
        websearch._parse_response(response, "test query")


def test_parse_skips_non_message_entries(websearch):
    response = _make_response(
        outputs=[MessageOutputEntry(content=[TextChunk(text="Answer")])]
    )
    result = websearch._parse_response(response, "test query")
    assert result.answer == "Answer"


@pytest.mark.asyncio
async def test_run_missing_api_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    config = WebSearchConfig()
    ws = WebSearch(config_getter=lambda: config, state=BaseToolState())
    with pytest.raises(ToolError, match="MISTRAL_API_KEY"):
        await collect_result(ws.run(WebSearchArgs(query="test")))


@pytest.mark.asyncio
async def test_run_uses_mistral_provider_api_key_env_var(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "wrong-key")
    monkeypatch.setenv("TEST_API_KEY", "provider-key")
    config = WebSearchConfig()
    ws = WebSearch(config_getter=lambda: config, state=BaseToolState())
    ctx = _ctx_with_config(
        build_test_vibe_config(providers=[_mistral_provider("TEST_API_KEY")])
    )
    response = _make_response(content=[TextChunk(text="The answer")])

    with patch("vibe.core.tools.builtins.websearch.Mistral") as mistral_cls:
        client = mistral_cls.return_value
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.beta.conversations.start_async = AsyncMock(return_value=response)

        result = await collect_result(ws.run(WebSearchArgs(query="test query"), ctx))

    assert result.answer == "The answer"
    assert mistral_cls.call_args.kwargs["api_key"] == "provider-key"
    assert mistral_cls.call_args.kwargs["server_url"] == "https://on-prem.example.com"
    assert mistral_cls.call_args.kwargs["timeout_ms"] == 120000


@pytest.mark.asyncio
async def test_run_falls_back_to_default_api_key_env_var_when_provider_env_var_empty(
    monkeypatch,
):
    monkeypatch.setenv("MISTRAL_API_KEY", "fallback-key")
    config = WebSearchConfig()
    ws = WebSearch(config_getter=lambda: config, state=BaseToolState())
    ctx = _ctx_with_config(build_test_vibe_config(providers=[_mistral_provider("")]))
    response = _make_response(content=[TextChunk(text="The answer")])

    with patch("vibe.core.tools.builtins.websearch.Mistral") as mistral_cls:
        client = mistral_cls.return_value
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.beta.conversations.start_async = AsyncMock(return_value=response)

        result = await collect_result(ws.run(WebSearchArgs(query="test query"), ctx))

    assert result.answer == "The answer"
    assert mistral_cls.call_args.kwargs["api_key"] == "fallback-key"


@pytest.mark.asyncio
async def test_run_reports_configured_api_key_env_var_when_missing(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "fallback-key")
    monkeypatch.setenv("TEST_API_KEY", "provider-key")
    ctx = _ctx_with_config(
        build_test_vibe_config(providers=[_mistral_provider("TEST_API_KEY")])
    )
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    config = WebSearchConfig()
    ws = WebSearch(config_getter=lambda: config, state=BaseToolState())

    with pytest.raises(ToolError, match="TEST_API_KEY"):
        await collect_result(ws.run(WebSearchArgs(query="test"), ctx))


@pytest.mark.asyncio
async def test_run_returns_parsed_result(websearch):
    response = _make_response(
        content=[
            TextChunk(text="The answer"),
            ToolReferenceChunk(
                tool="web_search", title="Source", url="https://example.com"
            ),
        ]
    )

    mock_start = AsyncMock(return_value=response)
    with patch.object(Mistral, "beta", create=True) as mock_beta:
        mock_beta.conversations.start_async = mock_start
        with patch.object(Mistral, "__aenter__", return_value=None):
            with patch.object(Mistral, "__aexit__", return_value=None):
                result = await collect_result(
                    websearch.run(WebSearchArgs(query="test query"))
                )

    assert result.query == "test query"
    assert result.answer == "The answer"
    assert len(result.sources) == 1
    assert result.sources[0].url == "https://example.com"


@pytest.mark.asyncio
async def test_run_sdk_error_wrapped(websearch):
    from unittest.mock import Mock

    import httpx

    mock_response = Mock(spec=httpx.Response)
    mock_response.status_code = 500
    mock_response.text = "error"
    mock_response.headers = httpx.Headers({"content-type": "application/json"})

    with patch.object(Mistral, "beta", create=True) as mock_beta:
        mock_beta.conversations.start_async = AsyncMock(
            side_effect=SDKError("API failed", mock_response)
        )
        with patch.object(Mistral, "__aenter__", return_value=None):
            with patch.object(Mistral, "__aexit__", return_value=None):
                with pytest.raises(ToolError, match="Mistral API error"):
                    await collect_result(websearch.run(WebSearchArgs(query="test")))


def test_resolve_server_url_no_ctx(websearch):
    assert websearch._resolve_server_url(None) is None


def test_resolve_server_url_no_agent_manager(websearch):
    ctx = InvokeContext(tool_call_id="t1", agent_manager=None)
    assert websearch._resolve_server_url(ctx) is None


def test_resolve_server_url_with_mistral_provider(websearch):
    ctx = _ctx_with_config(build_test_vibe_config(providers=[_mistral_provider()]))
    assert websearch._resolve_server_url(ctx) == "https://on-prem.example.com"


def test_resolve_server_url_with_default_provider(websearch):
    ctx = _ctx_with_config(
        build_test_vibe_config(
            providers=[_mistral_provider(api_base="https://api.mistral.ai/v1")]
        )
    )
    assert websearch._resolve_server_url(ctx) == "https://api.mistral.ai"


def test_resolve_server_url_no_mistral_provider(websearch):
    ctx = _ctx_with_config(
        build_test_vibe_config(active_model="local", providers=[_llamacpp_provider()])
    )
    assert websearch._resolve_server_url(ctx) is None


def test_is_available_with_key(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "key")
    assert WebSearch.is_available() is True


def test_is_available_without_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    assert WebSearch.is_available() is False


def test_is_available_uses_mistral_provider_api_key_env_var(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "fallback-key")
    monkeypatch.setenv("TEST_API_KEY", "provider-key")
    config = build_test_vibe_config(providers=[_mistral_provider("TEST_API_KEY")])
    monkeypatch.delenv("TEST_API_KEY", raising=False)

    assert WebSearch.is_available(config) is False

    monkeypatch.setenv("TEST_API_KEY", "provider-key")
    assert WebSearch.is_available(config) is True


def test_is_available_uses_non_active_mistral_provider(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "provider-key")
    config = build_test_vibe_config(
        active_model="local",
        providers=[_llamacpp_provider(), _mistral_provider("TEST_API_KEY")],
    )
    monkeypatch.delenv("TEST_API_KEY", raising=False)

    assert WebSearch.is_available(config) is False

    monkeypatch.setenv("TEST_API_KEY", "provider-key")
    assert WebSearch.is_available(config) is True


def test_is_available_falls_back_to_default_api_key_env_var_without_mistral_provider(
    monkeypatch,
):
    monkeypatch.setenv("MISTRAL_API_KEY", "fallback-key")
    config = build_test_vibe_config(
        active_model="local", providers=[_llamacpp_provider()]
    )

    assert WebSearch.is_available(config) is True

    monkeypatch.delenv("MISTRAL_API_KEY")

    assert WebSearch.is_available(config) is False


def test_is_available_falls_back_to_default_api_key_env_var_when_provider_env_var_empty(
    monkeypatch,
):
    monkeypatch.setenv("MISTRAL_API_KEY", "fallback-key")
    config = build_test_vibe_config(providers=[_mistral_provider("")])

    assert WebSearch.is_available(config) is True

    monkeypatch.delenv("MISTRAL_API_KEY")

    assert WebSearch.is_available(config) is False


def test_tool_manager_websearch_availability_uses_provider_api_key_env_var(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setenv("TEST_API_KEY", "provider-key")
    config = build_test_vibe_config(providers=[_mistral_provider("TEST_API_KEY")])
    manager = ToolManager(lambda: config)

    assert "web_search" in manager.available_tools

    monkeypatch.delenv("TEST_API_KEY")
    assert "web_search" not in manager.available_tools


def test_tool_manager_websearch_availability_falls_back_without_mistral_provider(
    monkeypatch,
):
    monkeypatch.setenv("MISTRAL_API_KEY", "fallback-key")
    config = build_test_vibe_config(
        active_model="local", providers=[_llamacpp_provider()]
    )
    manager = ToolManager(lambda: config)

    assert "web_search" in manager.available_tools

    monkeypatch.delenv("MISTRAL_API_KEY")
    assert "web_search" not in manager.available_tools


def test_get_status_text():
    assert WebSearch.get_status_text() == "Searching the web"


def test_get_result_display_includes_query_and_pluralizes_sources():
    result = WebSearchResult(
        query="python async",
        answer="answer",
        sources=[
            WebSearchSource(title="Docs", url="https://docs.python.org"),
            WebSearchSource(title="Blog", url="https://blog.example.com"),
        ],
    )
    event = ToolResultEvent(
        tool_name="web_search", tool_call_id="t1", tool_class=WebSearch, result=result
    )

    display = WebSearch.get_result_display(event)

    assert display.success is True
    assert "python async" in display.message
    assert "2 sources" in display.message


def test_get_result_display_uses_singular_for_one_source():
    result = WebSearchResult(
        query="python",
        answer="answer",
        sources=[WebSearchSource(title="Docs", url="https://docs.python.org")],
    )
    event = ToolResultEvent(
        tool_name="web_search", tool_call_id="t1", tool_class=WebSearch, result=result
    )

    assert "1 source)" in WebSearch.get_result_display(event).message
