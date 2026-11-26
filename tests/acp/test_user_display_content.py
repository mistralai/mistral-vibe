from __future__ import annotations

from pydantic import ValidationError
import pytest

from vibe.acp.user_display_content import (
    USER_DISPLAY_CONTENT_META_KEY,
    parse_user_display_content_metadata,
)
from vibe.user_content import UserDisplayContent


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

    assert metadata == UserDisplayContent(
        version="1.0.0", host="mistral-vscode", content=[]
    )


def test_parse_rejects_invalid_metadata() -> None:
    with pytest.raises(ValidationError):
        parse_user_display_content_metadata({
            "version": 2,
            "host": "mistral-vscode",
            "content": [],
        })
