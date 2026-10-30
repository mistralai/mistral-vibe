"""A provider's prompt-too-long refusal makes a Vibe session warn, compact and answer."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from pathlib import Path
import tomllib
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
import tomli_w

from tests.app_server.backend_contract.conftest import connect_backend_contract_host
from tests.stubs.mistral_responses import mistral_completion
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.events import ServerWarning
from vibe.app_server.models import PublicCheckpointEntry, PublicMessageEntry
from vibe.app_server.protocol import ClientCapabilities, SessionOptions
from vibe.app_server.session import AppServerSession

# A first turn of about 18,000 tokens, on top of Vibe's system prompt and tools,
# in a 64,000-token window that compacts at 32,000: the second turn's request is
# under the threshold by Core's estimate, so only the provider's refusal shows it
# is too long. The history is large enough next to the system prompt and tools
# that the compacted context fits under the corrected estimate.
_CONTEXT_WINDOW = 64_000
_FIRST_REQUEST = "Remember this log line. " * 3_000

# Mistral's own reply to a prompt over the model's context window.
_PROMPT_TOO_LONG = {
    "object": "error",
    "message": (
        "Prompt contains 64100 tokens and 0 draft tokens, too large for model "
        "with 64000 maximum context length"
    ),
    "type": "invalid_request_message_error",
    "param": None,
    "code": "3051",
}


def _reply(content: str, *, prompt_tokens: int) -> httpx.Response:
    chunk = mistral_completion(
        content, prompt_tokens=prompt_tokens, completion_tokens=5
    )
    chunk["object"] = "chat.completion.chunk"
    choice = chunk["choices"][0]
    message = choice.pop("message")
    message.pop("tool_calls")
    choice["delta"] = message
    body = b"data: " + json.dumps(chunk).encode() + b"\n\ndata: [DONE]"
    return httpx.Response(
        200,
        stream=httpx.ByteStream(stream=body),
        headers={"Content-Type": "text/event-stream"},
    )


@pytest_asyncio.fixture
async def small_window_session(
    config_dir: Path,
    backend_contract_mistral_api: respx.Route,
    process_services: ProcessServices,
) -> AsyncIterator[AppServerSession]:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["models"][0]["max_context_length"] = _CONTEXT_WINDOW
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    session = await connection.host.open_session()
    try:
        yield session
    finally:
        await session.close()
        await connection.host.close()


@pytest.mark.asyncio
async def test_a_prompt_too_long_refusal_warns_then_compacts_and_answers(
    backend_contract_mistral_api: respx.Route, small_window_session: AppServerSession
) -> None:
    """*Prepare*: A Vibe session on the Mistral adapter behind a fake endpoint that
    answers the first turn, refuses the second turn's request as over the context
    window with HTTP 400, then summarizes and answers.
    *Do*: Run both turns.
    *Assert*: The second turn shows Core's request_too_long warning, Core then sends
    the compaction request and records a checkpoint, and the retried turn answers
    from the compacted context.
    """
    # Prepare
    payloads: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        call = len(payloads)
        if call == 1:
            return _reply("first", prompt_tokens=28_000)
        if call == 2:
            return httpx.Response(400, json=_PROMPT_TOO_LONG)
        if call == 3:
            return _reply("<summary>the user asked twice</summary>", prompt_tokens=60)
        return _reply("second", prompt_tokens=20)

    backend_contract_mistral_api.mock(side_effect=respond)
    _ = [event async for event in small_window_session.act(_FIRST_REQUEST)]

    # Do
    events = [event async for event in small_window_session.act("second request")]

    # Assert
    assert len(payloads) == 4
    refused, compaction, retried = payloads[1:]
    assert refused["messages"][-1]["content"] == "second request"
    # The compaction request asks for a summary instead of the turn's message, and
    # is refitted under the corrected estimate.
    assert compaction["messages"][-1]["role"] == "user"
    assert "second request" not in json.dumps(compaction["messages"][-1])
    assert len(json.dumps(compaction["messages"])) < len(
        json.dumps(refused["messages"])
    )
    assert "the user asked twice" in json.dumps(retried["messages"])
    assert retried["messages"][-1]["content"] == "second request"

    warnings = [
        event.params.warning.code
        for event in events
        if isinstance(event, ServerWarning)
    ]
    assert warnings == ["request_too_long"]
    history = small_window_session.history
    assert any(isinstance(entry, PublicCheckpointEntry) for entry in history)
    assistant = [
        entry.text
        for entry in history
        if isinstance(entry, PublicMessageEntry) and entry.role == "assistant"
    ]
    assert assistant[-1] == "second"
