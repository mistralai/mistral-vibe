from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer

class _StubVoiceHandler:
    def __init__(self, is_recording: bool) -> None:
        self.is_recording = is_recording
        self.stop_calls = 0

    async def start_recording(self) -> None:
        return

    def stop_recording(self) -> None:
        self.stop_calls += 1
        self.is_recording = False


@pytest.mark.asyncio
async def test_ctrl_s_starts_voice_transcription_worker() -> None:
    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        app.voice_handler = _StubVoiceHandler(is_recording=False)
        mic_indicator = app.query_one("#mic-indicator")
        assert mic_indicator.styles.display == "none"

        with patch.object(app, "run_worker") as run_worker, patch.object(
            app, "notify"
        ) as notify:
            await pilot.press("ctrl+s")

            run_worker.assert_called_once()
            assert run_worker.call_args.kwargs["name"] == "voice_transcription"
            notify.assert_called_once_with("Mic on")
            assert mic_indicator.styles.display == "block"

            coroutine = run_worker.call_args.args[0]
            coroutine.close()


@pytest.mark.asyncio
async def test_ctrl_s_stops_voice_transcription_when_recording() -> None:
    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        handler = _StubVoiceHandler(is_recording=True)
        app.voice_handler = handler
        mic_indicator = app.query_one("#mic-indicator")
        mic_indicator.styles.display = "block"

        with patch.object(app, "run_worker") as run_worker, patch.object(
            app, "notify"
        ) as notify:
            await pilot.press("ctrl+s")

        run_worker.assert_not_called()
        assert handler.stop_calls == 1
        notify.assert_called_once_with("Mic off")
        assert mic_indicator.styles.display == "none"


@pytest.mark.asyncio
async def test_voice_text_does_not_submit_on_polite_words() -> None:
    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        chat_input = app.query_one(ChatInputContainer)
        chat_input.value = "hello"

        app._on_voice_text("Please!")
        await pilot.pause(0.1)

        assert chat_input.value == "hello Please!"


@pytest.mark.asyncio
async def test_voice_fragmented_chunks_keep_words_and_spacing_clean() -> None:
    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        chat_input = app.query_one(ChatInputContainer)

        for chunk in ["Sal", "ut", "  Mist", "ral", ",", "  comment", "  ça", "  va", " ?"]:
            app._on_voice_text(chunk)

        await pilot.pause(0.1)

        assert chat_input.value == "Salut Mistral, comment ça va ?"
