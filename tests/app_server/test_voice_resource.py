"""Tests for the client-side ``voice/*`` resource: notification routing."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from vibe.app_server._model import ProtocolModel
from vibe.app_server._service_resources import VoiceNotificationListener, VoiceResource
from vibe.app_server.protocol import (
    VOICE_NARRATION_DONE_METHOD,
    VOICE_NARRATION_ERROR_METHOD,
    VOICE_NARRATION_PREP_METHOD,
    VOICE_TRANSCRIPTION_DELTA_METHOD,
    Notification,
    VoiceTranscriptionDeltaParams,
)


class RecordingListener(VoiceNotificationListener):
    """Every push it receives, with the hook that fired."""

    def __init__(self) -> None:
        self.hooks: list[tuple[str, ProtocolModel]] = []

    def on_transcription_delta(self, params: VoiceTranscriptionDeltaParams) -> None:
        self.hooks.append(("delta", params))

    def on_narration_prep(self, params: ProtocolModel) -> None:
        self.hooks.append(("prep", params))

    def on_narration_done(self, params: ProtocolModel) -> None:
        self.hooks.append(("done", params))

    def on_narration_error(self, params: ProtocolModel) -> None:
        self.hooks.append(("error", params))


def _resource() -> VoiceResource:
    return VoiceResource(MagicMock(), MagicMock())


def _push(method: str, params: dict[str, Any]) -> Notification:
    return Notification(method=method, params=params)


@pytest.mark.parametrize(
    ("method", "hook", "params"),
    [
        (VOICE_TRANSCRIPTION_DELTA_METHOD, "delta", {"text": "hello"}),
        (VOICE_TRANSCRIPTION_DELTA_METHOD, "delta", {"error": "boom"}),
        (VOICE_NARRATION_PREP_METHOD, "prep", {}),
        (VOICE_NARRATION_DONE_METHOD, "done", {}),
        (VOICE_NARRATION_ERROR_METHOD, "error", {"error": "failed"}),
    ],
)
@pytest.mark.asyncio
async def test_each_voice_push_reaches_the_listener(
    method: str, hook: str, params: dict[str, Any]
) -> None:
    resource = _resource()
    listener = RecordingListener()
    resource.add_listener(listener)

    consumed = await resource.consume_notification(_push(method, params))

    assert consumed is True
    assert [hook for hook, _ in listener.hooks] == [hook]
    received = listener.hooks[0][1]
    assert received.model_dump(mode="json", by_alias=True, exclude_none=True) == params


@pytest.mark.asyncio
async def test_a_push_nobody_listens_to_is_still_consumed() -> None:
    resource = _resource()

    consumed = await resource.consume_notification(
        _push(VOICE_NARRATION_DONE_METHOD, {})
    )

    assert consumed is True


@pytest.mark.asyncio
async def test_other_notifications_are_not_consumed() -> None:
    resource = _resource()
    listener = RecordingListener()
    resource.add_listener(listener)

    consumed = await resource.consume_notification(
        _push("session/updated", {"eventId": 1})
    )

    assert consumed is False
    assert listener.hooks == []


@pytest.mark.asyncio
async def test_removed_listeners_hear_nothing() -> None:
    resource = _resource()
    listener = RecordingListener()
    resource.add_listener(listener)
    resource.remove_listener(listener)

    consumed = await resource.consume_notification(
        _push(VOICE_NARRATION_PREP_METHOD, {})
    )

    assert consumed is True
    assert listener.hooks == []
