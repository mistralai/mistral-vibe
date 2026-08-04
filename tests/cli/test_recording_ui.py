from __future__ import annotations

import pytest
from textual.containers import Horizontal

from tests.conftest import build_test_vibe_app
from tests.stubs.fake_voice_manager import FakeVoiceManager
from vibe.cli.textual_ui.recording.recording_indicator import RecordingIndicator
from vibe.cli.textual_ui.widgets.chat_input.body import ChatInputBody
from vibe.cli.voice_manager.voice_manager_port import TranscribeState


@pytest.mark.asyncio
async def test_starting_recording_ui_twice_does_not_duplicate_indicator() -> None:
    app = build_test_vibe_app(voice_manager=FakeVoiceManager(is_voice_ready=True))
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        body = app.query_one(ChatInputBody)
        body.on_transcribe_state_change(TranscribeState.RECORDING)
        await pilot.pause(0.1)
        body.on_transcribe_state_change(TranscribeState.RECORDING)
        await pilot.pause(0.1)

        assert len(body.query(RecordingIndicator)) == 1
        assert body._recording_indicator is not None


@pytest.mark.asyncio
async def test_two_recording_indicators_can_coexist_without_duplicate_ids() -> None:
    voice_manager = FakeVoiceManager(is_voice_ready=True)
    app = build_test_vibe_app(voice_manager=voice_manager)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        container = app.query_one(ChatInputBody).query_one(Horizontal)
        container.mount(RecordingIndicator(voice_manager))
        container.mount(RecordingIndicator(voice_manager))
        await pilot.pause(0.1)

        assert len(app.query(RecordingIndicator)) == 2
