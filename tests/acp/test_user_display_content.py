from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from acp.schema import TextContentBlock
from pydantic import ValidationError
import pytest

from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.acp.exceptions import InvalidRequestError
from vibe.acp.user_display_content import (
    USER_DISPLAY_CONTENT_META_KEY,
    parse_user_display_content_metadata,
)
from vibe.core.session.session_loader import SessionLoader
from vibe.core.types import Role, UserDisplayContentMetadata


def _metadata_payload() -> dict[str, object]:
    return {
        "version": "1.0.0",
        "host": "mistral-vscode",
        "content": [
            {"type": "text", "text": "Look at "},
            {
                "type": "workspace_mention",
                "kind": "file",
                "uri": "file:///repo/src/app.ts",
                "name": "app.ts",
            },
        ],
    }


def _metadata_kwargs(value: object) -> dict[str, Any]:
    return {USER_DISPLAY_CONTENT_META_KEY: value}


def test_user_display_content_meta_key_is_snake_case() -> None:
    assert USER_DISPLAY_CONTENT_META_KEY == "user_display_content"


def test_parse_returns_none_when_metadata_is_missing() -> None:
    assert parse_user_display_content_metadata(None) is None


def test_parse_validates_present_metadata() -> None:
    metadata = parse_user_display_content_metadata({
        "version": "1.0.0",
        "host": "mistral-vscode",
        "content": [],
    })

    assert metadata == UserDisplayContentMetadata(
        version="1.0.0", host="mistral-vscode", content=[]
    )


def test_parse_rejects_invalid_metadata() -> None:
    with pytest.raises(ValidationError):
        parse_user_display_content_metadata({
            "version": 2,
            "host": "mistral-vscode",
            "content": [],
        })


@pytest.mark.asyncio
async def test_prompt_attaches_user_display_content_to_user_message(
    acp_agent_loop: VibeAcpAgentLoop, backend: FakeBackend
) -> None:
    session_response = await acp_agent_loop.new_session(
        cwd=str(Path.cwd()), mcp_servers=[]
    )
    payload = _metadata_payload()

    response = await acp_agent_loop.prompt(
        prompt=[TextContentBlock(type="text", text="Look at app.ts")],
        session_id=session_response.session_id,
        **_metadata_kwargs(payload),
    )

    assert response.stop_reason == "end_turn"
    user_message = next(
        (msg for msg in backend.requests_messages[0] if msg.role == Role.user), None
    )
    assert user_message is not None
    assert user_message.content == "Look at app.ts"
    assert (
        user_message.user_display_content
        == UserDisplayContentMetadata.model_validate(payload)
    )


@pytest.mark.asyncio
async def test_prompt_rejects_invalid_user_display_content(
    acp_agent_loop: VibeAcpAgentLoop,
) -> None:
    session_response = await acp_agent_loop.new_session(
        cwd=str(Path.cwd()), mcp_servers=[]
    )

    with pytest.raises(InvalidRequestError, match="Invalid user display content"):
        await acp_agent_loop.prompt(
            prompt=[TextContentBlock(type="text", text="Look at app.ts")],
            session_id=session_response.session_id,
            **_metadata_kwargs({"version": 2, "host": "mistral-vscode", "content": []}),
        )


@pytest.mark.asyncio
async def test_prompt_persists_user_display_content(
    acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
    temp_session_dir: Path,
) -> None:
    acp_agent, _client = acp_agent_with_session_config
    session_response = await acp_agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    payload = _metadata_payload()

    await acp_agent.prompt(
        prompt=[TextContentBlock(type="text", text="Look at app.ts")],
        session_id=session_response.session_id,
        **_metadata_kwargs(payload),
    )

    session_dir = next(temp_session_dir.glob("session_*"))
    messages_file = session_dir / "messages.jsonl"
    messages = [
        json.loads(line)
        for line in messages_file.read_text(encoding="utf-8").splitlines()
    ]
    user_message = next(msg for msg in messages if msg["role"] == "user")

    assert user_message["user_display_content"] == payload

    loaded_messages, _metadata = SessionLoader.load_session(session_dir)
    loaded_user_message = next(msg for msg in loaded_messages if msg.role == Role.user)
    assert loaded_user_message.user_display_content == (
        UserDisplayContentMetadata.model_validate(payload)
    )
