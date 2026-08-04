from __future__ import annotations

import asyncio
from typing import Any, cast

from textual.pilot import Pilot

from tests.mock.utils import mock_llm_chunk
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp, default_config
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.app_config import build_test_app_config
from tests.stubs.fake_audio_player import FakeAudioPlayer
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_summary_generator import FakeSummaryGenerator
from tests.stubs.fake_tts_client import FakeTTSClient
from vibe.cli.narrator_manager import NarratorManager, NarratorState
import vibe.cli.textual_ui.widgets.narrator_status as narrator_status_mod
from vibe.cli.textual_ui.widgets.narrator_status import NarratorStatus

narrator_status_mod.SHRINK_FRAMES = "█"
narrator_status_mod.BAR_FRAMES = ["▂▅▇"]
from vibe.cli.tts.tts_client_port import TTSResult


def _narrator_config():
    config = build_test_app_config(narrator_enabled=True)
    config.disable_welcome_banner_animation = True
    return config


class GatedSummaryGenerator(FakeSummaryGenerator):
    def __init__(self) -> None:
        super().__init__("Summary of the conversation")
        self._gate = asyncio.Event()
        self.gate = self._gate

    def release(self) -> None:
        self._gate.set()


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
        self.summary_gate = GatedSummaryGenerator()
        self.tts_gate = GatedTTSClient()
        self.fake_audio_player = FakeAudioPlayer()
        narrator_manager = NarratorManager(
            config_getter=_narrator_config,
            audio_player=self.fake_audio_player,
            summary_generator=self.summary_gate,
        )
        narrator_manager._tts_client = self.tts_gate
        super().__init__(
            config=default_config(narrator_enabled=True),
            backend=FakeBackend(
                mock_llm_chunk(
                    content="Hello! I can help you.",
                    prompt_tokens=10_000,
                    completion_tokens=2_500,
                )
            ),
            narrator_manager=narrator_manager,
        )


def test_snapshot_narrator_summarizing(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        app = cast(NarratorFlowApp, pilot.app)
        # Send message and wait for agent response to complete
        await pilot.press(*"Hello")
        await pilot.press("enter")
        await pilot.pause(0.5)
        # on_turn_end has fired, SUMMARIZING is set, summary backend is gated
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
        app.fake_audio_player.stop()
        narrator = cast(NarratorManager, app._narrator_manager)
        narrator._set_state(NarratorState.IDLE)
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_narrator_flow.py:NarratorFlowApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )
