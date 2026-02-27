from __future__ import annotations

from unittest.mock import AsyncMock, patch

import mistralai
import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.websearch import WebSearch, WebSearchArgs, WebSearchConfig


def _make_response(
    content: list | None = None, outputs: list | None = None
) -> mistralai.ConversationResponse:
    if outputs is None:
        outputs = [mistralai.MessageOutputEntry(content=content or [])]
    return mistralai.ConversationResponse(
        conversation_id="test",
        outputs=outputs,
        usage=mistralai.ConversationUsageInfo(
            prompt_tokens=10, completion_tokens=20, total_tokens=30
        ),
    )


@pytest.fixture
def websearch(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    config = WebSearchConfig()
    return WebSearch(config=config, state=BaseToolState())


def test_parse_text_chunks(websearch):
    response = _make_response(
        content=[mistralai.TextChunk(text="Hello "), mistralai.TextChunk(text="world")]
    )
    result = websearch._parse_response(response)
    assert result.answer == "Hello world"
    assert result.sources == []


def test_parse_sources_deduped(websearch):
    response = _make_response(
        content=[
            mistralai.TextChunk(text="Answer"),
            mistralai.ToolReferenceChunk(
                tool="web_search", title="Site A", url="https://a.com"
            ),
            mistralai.ToolReferenceChunk(
                tool="web_search", title="Site A duplicate", url="https://a.com"
            ),
            mistralai.ToolReferenceChunk(
                tool="web_search", title="Site B", url="https://b.com"
            ),
        ]
    )
    result = websearch._parse_response(response)
    assert result.answer == "Answer"
    assert len(result.sources) == 2
    assert result.sources[0].url == "https://a.com"
    assert result.sources[0].title == "Site A"
    assert result.sources[1].url == "https://b.com"


def test_parse_skips_source_without_url(websearch):
    response = _make_response(
        content=[
            mistralai.TextChunk(text="Answer"),
            mistralai.ToolReferenceChunk(tool="web_search", title="No URL"),
        ]
    )
    result = websearch._parse_response(response)
    assert result.sources == []


def test_parse_empty_text_raises(websearch):
    response = _make_response(content=[])
    with pytest.raises(ToolError, match="No text in agent response"):
        websearch._parse_response(response)


def test_parse_whitespace_only_raises(websearch):
    response = _make_response(content=[mistralai.TextChunk(text="   ")])
    with pytest.raises(ToolError, match="No text in agent response"):
        websearch._parse_response(response)


def test_parse_skips_non_message_entries(websearch):
    response = _make_response(
        outputs=[
            mistralai.MessageOutputEntry(content=[mistralai.TextChunk(text="Answer")])
        ]
    )
    result = websearch._parse_response(response)
    assert result.answer == "Answer"


@pytest.mark.asyncio
async def test_run_missing_api_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    config = WebSearchConfig()
    ws = WebSearch(config=config, state=BaseToolState())
    with pytest.raises(ToolError, match="MISTRAL_API_KEY"):
        await collect_result(ws.run(WebSearchArgs(query="test")))


@pytest.mark.asyncio
async def test_run_returns_parsed_result(websearch):
    response = _make_response(
        content=[
            mistralai.TextChunk(text="The answer"),
            mistralai.ToolReferenceChunk(
                tool="web_search", title="Source", url="https://example.com"
            ),
        ]
    )

    mock_start = AsyncMock(return_value=response)
    with patch.object(mistralai.Mistral, "beta", create=True) as mock_beta:
        mock_beta.conversations.start_async = mock_start
        with patch.object(mistralai.Mistral, "__aenter__", return_value=None):
            with patch.object(mistralai.Mistral, "__aexit__", return_value=None):
                result = await collect_result(
                    websearch.run(WebSearchArgs(query="test query"))
                )

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

    with patch.object(mistralai.Mistral, "beta", create=True) as mock_beta:
        mock_beta.conversations.start_async = AsyncMock(
            side_effect=mistralai.SDKError("API failed", mock_response)
        )
        with patch.object(mistralai.Mistral, "__aenter__", return_value=None):
            with patch.object(mistralai.Mistral, "__aexit__", return_value=None):
                with pytest.raises(ToolError, match="Mistral API error"):
                    await collect_result(websearch.run(WebSearchArgs(query="test")))


def test_is_available_with_key(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "key")
    assert WebSearch.is_available() is True


def test_is_available_without_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    assert WebSearch.is_available() is False


def test_get_status_text():
    assert WebSearch.get_status_text() == "Searching the web"
