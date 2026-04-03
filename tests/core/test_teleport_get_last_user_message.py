from __future__ import annotations

import pytest

from vibe.core.teleport.nuage import TeleportSession
from vibe.core.teleport.teleport import TeleportService


@pytest.fixture
def teleport_service() -> TeleportService:
    return TeleportService.__new__(TeleportService)


def test_returns_last_user_message(teleport_service: TeleportService) -> None:
    session = TeleportSession(
        messages=[
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
    )
    assert teleport_service._get_last_user_message(session) == "second"


def test_returns_none_when_no_user_messages(teleport_service: TeleportService) -> None:
    session = TeleportSession(
        messages=[
            {"role": "system", "content": "system prompt"},
            {"role": "assistant", "content": "hello"},
        ]
    )
    assert teleport_service._get_last_user_message(session) is None


def test_returns_none_for_empty_messages(teleport_service: TeleportService) -> None:
    session = TeleportSession(messages=[])
    assert teleport_service._get_last_user_message(session) is None


def test_skips_non_string_content(teleport_service: TeleportService) -> None:
    session = TeleportSession(
        messages=[
            {"role": "user", "content": [{"type": "text", "text": "block content"}]}
        ]
    )
    assert teleport_service._get_last_user_message(session) is None


def test_skips_empty_string_content(teleport_service: TeleportService) -> None:
    session = TeleportSession(messages=[{"role": "user", "content": ""}])
    assert teleport_service._get_last_user_message(session) is None


def test_skips_non_string_returns_earlier_string(
    teleport_service: TeleportService,
) -> None:
    session = TeleportSession(
        messages=[
            {"role": "user", "content": "valid message"},
            {"role": "user", "content": [{"type": "text", "text": "block"}]},
        ]
    )
    assert teleport_service._get_last_user_message(session) == "valid message"


def test_skips_missing_content_key(teleport_service: TeleportService) -> None:
    session = TeleportSession(messages=[{"role": "user"}])
    assert teleport_service._get_last_user_message(session) is None
