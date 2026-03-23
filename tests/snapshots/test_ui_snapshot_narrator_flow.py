from __future__ import annotations

import asyncio
from typing import Any, cast

from textual.pilot import Pilot

from tests.conftest import build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.fake_audio_player import FakeAudioPlayer
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_tts_client import FakeTTSClient
import vibe.cli.textual_ui.widgets.narrator_status as narrator_status_mod
from vibe.cli.textual_ui.widgets.narrator_status import NarratorState, NarratorStatus
from vibe.cli.turn_summary import TurnSummaryTracker

narrator_status_mod.SHRINK_FRAMES = "█"
narrator_status_mod.BAR_FRAMES = ["▂▅▇"]
from vibe.core.config import ModelConfig
from vibe.core.tts.tts_client_port import TTSResult
from vibe.core.types import LLMChunk

_TEST_MODEL = ModelConfig(name="test-model", provider="test", alias="test-model")


def _narrator_config():
    return build_test_vibe_config(
        narrator_enabled=True,
        disable_welcome_banner_animation=True,
        displayed_workdir="/test/workdir",
    )


class GatedBackend(FakeBackend):
    def __init__(self, chunks: LLMChunk) -> None:
        super().__init__(chunks)
        self._gate = asyncio.Event()

    def release(self) -> None:
        self._gate.set()

    async def complete(self, **kwargs: Any) -> LLMChunk:
        await self._gate.wait()
        return await super().complete(**kwargs)


class GatedTTSClient(FakeTTSClient):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._gate = asyncio.Event()

    def release(self) -> None:
        self._gate.set()

    async def speak(self, text: str) -> TTSResult:
        await self._gate.wait()
        return await super().speak(text)


class NarratorFlowApp(BaseSnapshotTestApp):
    def __init__(self) -> None:
        self.summary_gate = GatedBackend(
            mock_llm_chunk(content="Summary of the conversation")
        )
        self.tts_gate = GatedTTSClient()
        super().__init__(
            config=_narrator_config(),
            backend=FakeBackend(
                mock_llm_chunk(
                    content="Hello! I can help you.",
                    prompt_tokens=10_000,
                    completion_tokens=2_500,
                )
            ),
        )
        self._tts_client = self.tts_gate
        self._audio_player = FakeAudioPlayer()
        self._turn_summary = TurnSummaryTracker(
            backend=self.summary_gate,
            model=_TEST_MODEL,
            on_summary=self._on_turn_summary,
        )


def test_snapshot_narrator_summarizing(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        app = cast(NarratorFlowApp, pilot.app)
        # Send message and wait for agent response to complete
        await pilot.press(*"Hello")
        await pilot.press("enter")
        await pilot.pause(0.5)
        # end_turn has fired, SUMMARIZING is set, summary backend is gated
        assert app.summary_gate._gate.is_set() is False
        # Freeze animation at frame 0 for deterministic snapshot
        app.query_one(NarratorStatus)._stop_timer()

    assert snap_compare(
        "test_ui_snapshot_narrator_flow.py:NarratorFlowApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_narrator_speaking(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        app = cast(NarratorFlowApp, pilot.app)
        await pilot.press(*"Hello")
        await pilot.press("enter")
        await pilot.pause(0.5)
        # Release summary gate → summary resolves → speak task starts → blocks on TTS gate
        app.summary_gate.release()
        await pilot.pause(0.2)
        # Release TTS gate → TTS resolves → SPEAKING set
        app.tts_gate.release()
        await pilot.pause(0.2)
        # Freeze animation at frame 0 for deterministic snapshot
        app.query_one(NarratorStatus)._stop_timer()

    assert snap_compare(
        "test_ui_snapshot_narrator_flow.py:NarratorFlowApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_narrator_idle_after_speaking(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        app = cast(NarratorFlowApp, pilot.app)
        await pilot.press(*"Hello")
        await pilot.press("enter")
        await pilot.pause(0.5)
        # Release both gates to reach SPEAKING
        app.summary_gate.release()
        await pilot.pause(0.2)
        app.tts_gate.release()
        await pilot.pause(0.2)
        # Simulate playback finishing (same thread, so call directly)
        app._audio_player.stop()
        app._set_narrator_state(NarratorState.IDLE)
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_narrator_flow.py:NarratorFlowApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )
