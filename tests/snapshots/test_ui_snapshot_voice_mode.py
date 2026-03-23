from __future__ import annotations

from textual.pilot import Pilot

from tests.conftest import build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_voice_manager import FakeVoiceManager
from vibe.cli.textual_ui.widgets.chat_input.body import ChatInputBody


class VoiceEnableApp(BaseSnapshotTestApp):
    def __init__(self) -> None:
        super().__init__(voice_manager=FakeVoiceManager(is_voice_ready=False))


class VoiceDisableApp(BaseSnapshotTestApp):
    def __init__(self) -> None:
        config = build_test_vibe_config(
            disable_welcome_banner_animation=True,
            displayed_workdir="/test/workdir",
            voice_mode_enabled=True,
        )
        super().__init__(
            config=config, voice_manager=FakeVoiceManager(is_voice_ready=True)
        )


class RecordingActiveApp(BaseSnapshotTestApp):
    """Voice enabled — used for recording snapshot tests."""

    def __init__(self) -> None:
        super().__init__(voice_manager=FakeVoiceManager(is_voice_ready=True))


class VoiceDisabledRecordingAttemptApp(BaseSnapshotTestApp):
    """Voice disabled — ctrl+r should be ignored."""

    def __init__(self) -> None:
        super().__init__(voice_manager=FakeVoiceManager(is_voice_ready=False))


class RecordingThenConversationApp(BaseSnapshotTestApp):
    """Voice enabled + FakeBackend that returns an LLM response."""

    def __init__(self) -> None:
        fake_backend = FakeBackend(mock_llm_chunk(content="I'm ready to help you."))
        super().__init__(
            voice_manager=FakeVoiceManager(is_voice_ready=True), backend=fake_backend
        )


def test_snapshot_voice_enable(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"/voice")
        await pilot.press("enter")
        await pilot.pause(0.4)
        await pilot.press("space")
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.4)

    assert snap_compare(
        "test_ui_snapshot_voice_mode.py:VoiceEnableApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_voice_disable(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"/voice")
        await pilot.press("enter")
        await pilot.pause(0.4)
        await pilot.press("space")
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.4)

    assert snap_compare(
        "test_ui_snapshot_voice_mode.py:VoiceDisableApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_recording_indicator_shown(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"hello")
        await pilot.pause(0.2)
        await pilot.press("ctrl+r")
        await pilot.pause(0.5)

    assert snap_compare(
        "test_ui_snapshot_voice_mode.py:RecordingActiveApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_recording_stopped_after_keypress(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"hello")
        await pilot.pause(0.2)
        await pilot.press("ctrl+r")
        await pilot.pause(0.5)
        await pilot.press("a")
        await pilot.pause(0.5)

    assert snap_compare(
        "test_ui_snapshot_voice_mode.py:RecordingActiveApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_recording_not_started_when_voice_disabled(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"hello")
        await pilot.pause(0.2)
        await pilot.press("ctrl+r")
        await pilot.pause(0.5)

    assert snap_compare(
        "test_ui_snapshot_voice_mode.py:VoiceDisabledRecordingAttemptApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_recording_then_conversation(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"hello")
        await pilot.pause(0.2)
        await pilot.press("ctrl+r")
        await pilot.pause(0.5)
        await pilot.press("a")
        await pilot.pause(0.5)
        await pilot.press("enter")
        await pilot.pause(0.4)

    assert snap_compare(
        "test_ui_snapshot_voice_mode.py:RecordingThenConversationApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_transcription_populates_text_area(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press("ctrl+r")
        await pilot.pause(0.3)
        pilot.app.query_one(ChatInputBody).on_transcribe_text("Hello world from voice")
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_voice_mode.py:RecordingActiveApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )
