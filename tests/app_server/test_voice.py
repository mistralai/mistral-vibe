"""Tests for the ``voice/*`` app-server capability."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._model import ProtocolModel
from vibe.app_server._voice_controller import VOICE_METHODS, VoiceController
from vibe.app_server.config import ConfigView
from vibe.app_server.protocol import SERVER_METHODS
from vibe.cli.narrator_manager.narrator_manager_port import (
    NarratorManagerListener,
    NarratorState,
)
from vibe.cli.voice_manager.voice_manager_port import (
    TranscribeState,
    VoiceManagerListener,
)

_SESSION_ID = "session-1"


class FakeVoiceManager:
    def __init__(self) -> None:
        self.listeners: list[VoiceManagerListener] = []
        self.transcribe_state = TranscribeState.IDLE
        self.start_calls = 0
        self.cancel_calls = 0
        self.closed = False

    def add_listener(self, listener: VoiceManagerListener) -> None:
        self.listeners.append(listener)

    def remove_listener(self, listener: VoiceManagerListener) -> None:
        self.listeners.remove(listener)

    def start_recording(self) -> None:
        self.start_calls += 1

    async def stop_recording(self) -> None:
        pass

    def cancel_recording(self) -> None:
        self.cancel_calls += 1

    async def close(self) -> None:
        self.closed = True


class FakeNarratorManager:
    def __init__(self) -> None:
        self.listeners: list[NarratorManagerListener] = []
        self.turn_starts: list[str] = []
        self.assistant_texts: list[str] = []
        self.turn_ends = 0
        self.cancels = 0
        self.closed = False

    def add_listener(self, listener: NarratorManagerListener) -> None:
        self.listeners.append(listener)

    def remove_listener(self, listener: NarratorManagerListener) -> None:
        self.listeners.remove(listener)

    def on_turn_start(self, user_message: str) -> None:
        self.turn_starts.append(user_message)

    def on_assistant_text(self, content: str) -> None:
        self.assistant_texts.append(content)

    def on_turn_end(self) -> None:
        self.turn_ends += 1

    def cancel(self) -> None:
        self.cancels += 1

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeNotifier:
    """The notification sink, recording every push."""

    notifications: list[tuple[str, ProtocolModel]] = field(default_factory=list)

    async def __call__(self, method: str, params: ProtocolModel) -> None:
        self.notifications.append((method, params))


class FakeTurnSummaries:
    """The turn summaries the narration asks for."""

    async def summarize(
        self,
        *,
        user_message: str,
        assistant_text: str,
        error: str | None,
        message_id: str | None,
    ) -> str | None:
        return "summary"


def _require_session(session_id: str) -> None:
    assert session_id == _SESSION_ID


def _unused_config() -> ConfigView:
    # The patched audio factories below never read the config.
    raise AssertionError("the patched factories never read the config")


def _controller(notifier: FakeNotifier | None = None) -> VoiceController:
    return VoiceController(
        require_session=_require_session,
        config_getter=_unused_config,
        summary_generator=FakeTurnSummaries(),
        telemetry=None,
        notify=notifier or FakeNotifier(),
    )


async def _drain() -> None:
    for _ in range(3):
        await asyncio.sleep(0)


def _notification_methods(notifier: FakeNotifier) -> list[str]:
    return [method for method, _ in notifier.notifications]


def _wire(**params: str) -> dict[str, str]:
    return {"sessionId": _SESSION_ID, **params}


@pytest.fixture
def managers(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FakeVoiceManager, FakeNarratorManager, list[str]]:
    """The session's audio managers, plus the record of their creations."""
    voice = FakeVoiceManager()
    narrator = FakeNarratorManager()
    created: list[str] = []

    import vibe.cli.lazy_audio_managers as lazy

    def fake_voice_manager(**_kwargs: Any) -> FakeVoiceManager:
        created.append("voice")
        return voice

    def fake_narrator_manager(**_kwargs: Any) -> FakeNarratorManager:
        created.append("narrator")
        return narrator

    monkeypatch.setattr(lazy, "create_default_voice_manager", fake_voice_manager)
    monkeypatch.setattr(lazy, "create_default_narrator_manager", fake_narrator_manager)
    return voice, narrator, created


def test_voice_methods_are_advertised() -> None:
    assert VOICE_METHODS <= set(SERVER_METHODS)


@pytest.mark.asyncio
async def test_transcribe_start_materializes_managers_once_and_records(
    managers: tuple[FakeVoiceManager, FakeNarratorManager, list[str]],
) -> None:
    voice, _, created = managers
    controller = _controller()

    first = await controller.dispatch("voice/transcribeStart", _wire())
    second = await controller.dispatch("voice/transcribeCancel", _wire())

    assert first.response.model_dump(mode="json", by_alias=True, exclude_none=True) == {
        "ok": True
    }
    assert second.response.model_dump(
        mode="json", by_alias=True, exclude_none=True
    ) == {"ok": True}
    assert voice.start_calls == 1
    assert created == ["voice", "narrator"]


@pytest.mark.asyncio
async def test_transcribe_start_refuses_when_already_recording(
    managers: tuple[FakeVoiceManager, FakeNarratorManager, list[str]],
) -> None:
    voice, _, _ = managers
    voice.transcribe_state = TranscribeState.RECORDING
    controller = _controller()

    result = await controller.dispatch("voice/transcribeStart", _wire())

    assert result.response.model_dump(
        mode="json", by_alias=True, exclude_none=True
    ) == {"ok": False, "error": "Transcription already in progress"}
    assert voice.start_calls == 0


@pytest.mark.asyncio
async def test_transcribe_stop_before_start_answers_ok_with_empty_text() -> None:
    controller = _controller()

    result = await controller.dispatch("voice/transcribeStop", _wire())

    assert result.response.model_dump(
        mode="json", by_alias=True, exclude_none=True
    ) == {"ok": True, "text": ""}


@pytest.mark.asyncio
async def test_transcribe_cancel_before_start_answers_ok() -> None:
    controller = _controller()

    result = await controller.dispatch("voice/transcribeCancel", _wire())

    assert result.response.model_dump(
        mode="json", by_alias=True, exclude_none=True
    ) == {"ok": True}


@pytest.mark.asyncio
async def test_narrate_cancels_previous_playback_then_narrates(
    managers: tuple[FakeVoiceManager, FakeNarratorManager, list[str]],
) -> None:
    _, narrator, _ = managers
    controller = _controller()

    result = await controller.dispatch(
        "voice/narrate", _wire(userMessage="hello", assistantText="hi there")
    )

    assert result.response.model_dump(
        mode="json", by_alias=True, exclude_none=True
    ) == {"ok": True}
    assert narrator.cancels == 1
    assert narrator.turn_starts == ["hello"]
    assert narrator.assistant_texts == ["hi there"]
    assert narrator.turn_ends == 1


@pytest.mark.asyncio
async def test_transcription_listener_emits_delta_notifications(
    managers: tuple[FakeVoiceManager, FakeNarratorManager, list[str]],
) -> None:
    voice, _, _ = managers
    notifier = FakeNotifier()
    controller = _controller(notifier)
    await controller.dispatch("voice/transcribeStart", _wire())

    assert voice.listeners, "the controller never attached its listener"
    voice.listeners[0].on_transcribe_text("hello wor")
    voice.listeners[0].on_transcribe_error("boom")
    await _drain()

    assert _notification_methods(notifier) == [
        "voice/transcriptionDelta",
        "voice/transcriptionDelta",
    ]
    assert notifier.notifications[0][1].model_dump(
        mode="json", by_alias=True, exclude_none=True
    ) == {"text": "hello wor"}
    assert notifier.notifications[1][1].model_dump(
        mode="json", by_alias=True, exclude_none=True
    ) == {"error": "boom"}


@pytest.mark.asyncio
async def test_narrator_listener_emits_prep_done_and_error(
    managers: tuple[FakeVoiceManager, FakeNarratorManager, list[str]],
) -> None:
    _, narrator, _ = managers
    notifier = FakeNotifier()
    controller = _controller(notifier)
    await controller.dispatch("voice/narrate", _wire())
    listener = narrator.listeners[0]

    # Prep, then speech that settles to done.
    listener.on_narrator_state_change(NarratorState.SUMMARIZING)
    await _drain()
    listener.on_narrator_state_change(NarratorState.SPEAKING)
    listener.on_narrator_state_change(NarratorState.IDLE)
    await _drain()
    # A new narration that never reaches speaking fails during preparation.
    listener.on_narrator_state_change(NarratorState.SUMMARIZING)
    await _drain()
    listener.on_narrator_state_change(NarratorState.IDLE)
    await _drain()

    assert _notification_methods(notifier) == [
        "voice/narrationPrep",
        "voice/narrationDone",
        "voice/narrationPrep",
        "voice/narrationError",
    ]
    assert notifier.notifications[-1][1].model_dump(mode="json", by_alias=True) == {
        "error": "Narration failed during preparation"
    }


@pytest.mark.asyncio
async def test_narrate_cancel_marks_canceling_and_emits_done(
    managers: tuple[FakeVoiceManager, FakeNarratorManager, list[str]],
) -> None:
    _, narrator, _ = managers
    notifier = FakeNotifier()
    controller = _controller(notifier)
    await controller.dispatch("voice/narrate", _wire())

    result = await controller.dispatch("voice/narrateCancel", _wire())

    assert result.response.model_dump(
        mode="json", by_alias=True, exclude_none=True
    ) == {"ok": True}
    # One from ``narrate``'s fresh-turn cancel, one from the explicit cancel.
    assert narrator.cancels == 2
    await _drain()
    assert _notification_methods(notifier) == ["voice/narrationDone"]

    # The next narration's lifecycle flows through the listener unharmed.
    listener = narrator.listeners[0]
    listener.on_narrator_state_change(NarratorState.SUMMARIZING)
    await _drain()
    listener.on_narrator_state_change(NarratorState.SPEAKING)
    listener.on_narrator_state_change(NarratorState.IDLE)
    await _drain()
    assert _notification_methods(notifier) == [
        "voice/narrationDone",
        "voice/narrationPrep",
        "voice/narrationDone",
    ]


@pytest.mark.asyncio
async def test_close_closes_both_managers(
    managers: tuple[FakeVoiceManager, FakeNarratorManager, list[str]],
) -> None:
    voice, narrator, _ = managers
    controller = _controller()
    await controller.dispatch("voice/narrate", _wire())

    await controller.close()

    assert voice.closed
    assert narrator.closed


@pytest.mark.asyncio
async def test_unknown_voice_method_is_not_found() -> None:
    controller = _controller()

    with pytest.raises(RequestFailure):
        await controller.dispatch("voice/warp", _wire())


@pytest.mark.asyncio
async def test_wrong_session_is_refused(
    managers: tuple[FakeVoiceManager, FakeNarratorManager, list[str]],
) -> None:
    controller = _controller()

    with pytest.raises(AssertionError):
        await controller.dispatch("voice/narrate", {"sessionId": "other"})
