from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_audio_player import FakeAudioPlayer
from tests.stubs.fake_tts_client import FakeTTSClient
from vibe.cli.narrator_manager import NarratorManager, NarratorState
from vibe.cli.turn_summary import TurnSummaryResult
from vibe.core.tts.tts_client_port import TTSResult


def _make_manager(
    *,
    narrator_enabled: bool = True,
    telemetry_client: MagicMock | None = None,
    tts_client: FakeTTSClient | None = None,
) -> tuple[NarratorManager, FakeAudioPlayer]:
    config = build_test_vibe_config(narrator_enabled=narrator_enabled)
    audio_player = FakeAudioPlayer()
    manager = NarratorManager(
        config_getter=lambda: config,
        audio_player=audio_player,
        telemetry_client=telemetry_client,
    )
    manager._tts_client = tts_client or FakeTTSClient(
        result=TTSResult(audio_data=b"fake-audio")
    )
    return manager, audio_player


def _find_telemetry_calls(mock: MagicMock, event_name: str) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for call in mock.send_telemetry_event.call_args_list:
        if call[0][0] == event_name:
            results.append(call[0][1])
    return results


class TestTelemetryTracking:
    @pytest.mark.asyncio
    async def test_requested_event_on_turn_end(self) -> None:
        mock_telemetry = MagicMock()
        manager, _ = _make_manager(telemetry_client=mock_telemetry)
        manager._turn_summary.start_turn("test")
        manager.on_turn_end()

        calls = _find_telemetry_calls(mock_telemetry, "vibe.read_aloud.requested")
        assert len(calls) == 1
        assert calls[0]["trigger"] == "autoplay_next_message"
        assert isinstance(calls[0]["read_aloud_session_id"], str)
        assert len(calls[0]["read_aloud_session_id"]) == 36

    def test_no_requested_event_when_narrator_disabled(self) -> None:
        mock_telemetry = MagicMock()
        manager, _ = _make_manager(
            narrator_enabled=False, telemetry_client=mock_telemetry
        )
        manager.on_turn_end()

        calls = _find_telemetry_calls(mock_telemetry, "vibe.read_aloud.requested")
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_play_started_on_speak(self) -> None:
        mock_telemetry = MagicMock()
        manager, _ = _make_manager(telemetry_client=mock_telemetry)

        manager._turn_summary.start_turn("test")
        manager.on_turn_end()
        manager._on_turn_summary(
            TurnSummaryResult(
                summary="Test summary", generation=manager._turn_summary.generation
            )
        )
        await asyncio.sleep(0)

        calls = _find_telemetry_calls(mock_telemetry, "vibe.read_aloud.play_started")
        assert len(calls) == 1
        assert calls[0]["speed_selection"] is None
        assert isinstance(calls[0]["time_to_first_read_s"], float)
        assert calls[0]["time_to_first_read_s"] >= 0.0

    @pytest.mark.asyncio
    async def test_ended_completed_on_playback_finished(self) -> None:
        mock_telemetry = MagicMock()
        manager, _ = _make_manager(telemetry_client=mock_telemetry)

        manager._turn_summary.start_turn("test")
        manager.on_turn_end()
        manager._on_turn_summary(
            TurnSummaryResult(
                summary="Test summary", generation=manager._turn_summary.generation
            )
        )
        await asyncio.sleep(0)
        assert manager.state == NarratorState.SPEAKING

        manager._on_playback_finished()

        calls = _find_telemetry_calls(mock_telemetry, "vibe.read_aloud.ended")
        assert len(calls) == 1
        assert calls[0]["status"] == "completed"
        assert calls[0]["error_type"] is None
        assert calls[0]["speed_selection"] is None
        assert isinstance(calls[0]["elapsed_seconds"], float)
        assert calls[0]["elapsed_seconds"] >= 0.0

    @pytest.mark.asyncio
    async def test_ended_error_on_tts_failure(self) -> None:
        mock_telemetry = MagicMock()

        class FailingTTSClient:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            async def speak(self, text: str) -> TTSResult:
                raise RuntimeError("TTS failed")

            async def close(self) -> None:
                pass

        manager, _ = _make_manager(telemetry_client=mock_telemetry)
        manager._tts_client = FailingTTSClient()

        manager._turn_summary.start_turn("test")
        manager.on_turn_end()
        manager._on_turn_summary(
            TurnSummaryResult(
                summary="Test summary", generation=manager._turn_summary.generation
            )
        )
        await asyncio.sleep(0)

        calls = _find_telemetry_calls(mock_telemetry, "vibe.read_aloud.ended")
        assert len(calls) == 1
        assert calls[0]["status"] == "error"
        assert calls[0]["error_type"] == "RuntimeError"

    @pytest.mark.asyncio
    async def test_ended_canceled_on_cancel(self) -> None:
        mock_telemetry = MagicMock()
        manager, _ = _make_manager(telemetry_client=mock_telemetry)

        manager._turn_summary.start_turn("test")
        manager.on_turn_end()
        assert manager.state == NarratorState.SUMMARIZING

        manager.cancel()

        calls = _find_telemetry_calls(mock_telemetry, "vibe.read_aloud.ended")
        assert len(calls) == 1
        assert calls[0]["status"] == "canceled"

    @pytest.mark.asyncio
    async def test_cancel_during_speaking_fires_single_ended_event(self) -> None:
        mock_telemetry = MagicMock()
        manager, _ = _make_manager(telemetry_client=mock_telemetry)

        manager._turn_summary.start_turn("test")
        manager.on_turn_end()
        manager._on_turn_summary(
            TurnSummaryResult(
                summary="Test summary", generation=manager._turn_summary.generation
            )
        )
        await asyncio.sleep(0)
        assert manager.state == NarratorState.SPEAKING

        manager.cancel()
        # Simulate the delayed callback that would come from call_soon_threadsafe
        manager._on_playback_finished()

        calls = _find_telemetry_calls(mock_telemetry, "vibe.read_aloud.ended")
        assert len(calls) == 1
        assert calls[0]["status"] == "canceled"

    @pytest.mark.asyncio
    async def test_no_error_without_telemetry_client(self) -> None:
        manager, _ = _make_manager()
        manager._turn_summary.start_turn("test")
        manager.on_turn_end()
        manager._on_turn_summary(
            TurnSummaryResult(
                summary="Test summary", generation=manager._turn_summary.generation
            )
        )
        await asyncio.sleep(0)
        manager._on_playback_finished()
