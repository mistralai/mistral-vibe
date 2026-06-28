from __future__ import annotations

import math
from types import SimpleNamespace

from pydantic import ValidationError
import pytest

from vibe.core.types import LLMMessage, Role, UserDisplayContentMetadata


def _metadata() -> UserDisplayContentMetadata:
    return UserDisplayContentMetadata(
        version="1.0.0",
        host="mistral-vscode",
        content=[
            {"type": "text", "text": "Look at "},
            {
                "type": "workspace_mention",
                "kind": "file",
                "uri": "file:///repo/src/app.ts",
                "name": "app.ts",
            },
        ],
    )


def test_accepts_valid_metadata_and_preserves_host_owned_content() -> None:
    metadata = UserDisplayContentMetadata.model_validate({
        "version": "1.0.0",
        "host": "mistral-vscode",
        "content": [
            {"type": "text", "text": "Look at "},
            {
                "type": "workspace_mention",
                "kind": "file",
                "uri": "file:///repo/src/app.ts",
                "name": "app.ts",
                "automatic": False,
                "nested": {"line": 12, "tags": ["source", None]},
            },
        ],
    })

    assert metadata.version == "1.0.0"
    assert metadata.host == "mistral-vscode"
    assert metadata.content == [
        {"type": "text", "text": "Look at "},
        {
            "type": "workspace_mention",
            "kind": "file",
            "uri": "file:///repo/src/app.ts",
            "name": "app.ts",
            "automatic": False,
            "nested": {"line": 12, "tags": ["source", None]},
        },
    ]


def test_strips_metadata_strings_without_stripping_host_owned_content() -> None:
    metadata = UserDisplayContentMetadata.model_validate({
        "version": " 1.0.0 ",
        "host": " mistral-vscode ",
        "content": [{"type": "text", "text": " keep spaces "}],
    })

    assert metadata.version == "1.0.0"
    assert metadata.host == "mistral-vscode"
    assert metadata.content == [{"type": "text", "text": " keep spaces "}]


@pytest.mark.parametrize(
    "payload",
    [
        {"version": "   ", "host": "mistral-vscode", "content": []},
        {"version": 2, "host": "mistral-vscode", "content": []},
        {"version": "1.0.0", "host": "   ", "content": []},
        {"version": "1.0.0", "host": "mistral-vscode", "content": ["plain text"]},
        {"version": "1.0.0", "host": "mistral-vscode", "content": {"type": "text"}},
        {
            "version": "1.0.0",
            "host": "mistral-vscode",
            "content": [{"type": "text"}],
            "unexpected": True,
        },
        {
            "version": "1.0.0",
            "host": "mistral-vscode",
            "content": [{"type": "text", "value": object()}],
        },
        {
            "version": "1.0.0",
            "host": "mistral-vscode",
            "content": [{"type": "text", "value": math.nan}],
        },
    ],
)
def test_rejects_invalid_metadata(payload: object) -> None:
    with pytest.raises(ValidationError):
        UserDisplayContentMetadata.model_validate(payload)


def test_llm_message_round_trips_user_display_content() -> None:
    metadata = _metadata()
    message = LLMMessage(
        role=Role.user, content="Look at app.ts", user_display_content=metadata
    )

    dumped = message.model_dump(exclude_none=True, mode="json")
    loaded = LLMMessage.model_validate(dumped)

    assert dumped["user_display_content"] == metadata.model_dump(mode="json")
    assert loaded.user_display_content == metadata


def test_llm_message_keeps_old_sessions_without_user_display_content_valid() -> None:
    message = LLMMessage.model_validate({"role": "user", "content": "hello"})

    assert message.user_display_content is None


def test_llm_message_object_adapter_preserves_user_display_content() -> None:
    metadata = _metadata()

    message = LLMMessage.model_validate(
        SimpleNamespace(
            role=Role.user, content="Look at app.ts", user_display_content=metadata
        )
    )

    assert message.user_display_content == metadata
