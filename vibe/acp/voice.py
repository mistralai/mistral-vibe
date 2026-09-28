"""Thin ACP adapter over the CLI's VoiceManager and NarratorManager.

The ACP subprocess delegates all audio I/O to the same CLI components:
  - VoiceManager (AudioRecorder + MistralTranscribeClient) for dictation
  - NarratorManager (NarrationResource + MistralTTSClient + AudioPlayer) for narration

This file contains zero audio code — no miniaudio, no WebSocket, no TTS.
It just wires the CLI managers to ACP ext_method calls and translates
their listener callbacks into ext_notification pushes to the host.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from vibe.cli.lazy_audio_managers import (
    create_default_narrator_manager,
    create_default_voice_manager,
)
from vibe.cli.narrator_manager.narrator_manager_port import (
    NarratorManagerListener,
    NarratorManagerPort,
    NarratorState,
)
from vibe.cli.voice_manager.voice_manager_port import (
    VoiceManagerListener,
    VoiceManagerPort,
)
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from acp import Client

    from vibe.app_server._service_resources import NarrationResource
    from vibe.app_server.config import ConfigView
    from vibe.app_server.telemetry_port import ClientTelemetry


_TRANSCRIPTION_DELTA_METHOD = "voice/transcriptionDelta"
_NARRATION_PREP_METHOD = "voice/narrationPrep"
_NARRATION_DONE_METHOD = "voice/narrationDone"
_NARRATION_ERROR_METHOD = "voice/narrationError"


async def _safe_emit(
    client_getter: Callable[[], Client], method: str, params: dict | None = None
) -> None:
    """Send an ext_notification, swallowing all errors."""
    try:
        await client_getter().ext_notification(method, params or {})
    except Exception:
        logger.warning("Failed to emit %s", method, exc_info=True)


class _VoiceListener(VoiceManagerListener):
    """Bridges VoiceManager callbacks to ACP ext_notification."""

    def __init__(self, client_getter: Callable[[], Client]) -> None:
        self._client_getter = client_getter

    def on_transcribe_text(self, text: str) -> None:
        asyncio.ensure_future(
            _safe_emit(self._client_getter, _TRANSCRIPTION_DELTA_METHOD, {"text": text})
        )

    def on_transcribe_error(self, message: str) -> None:
        asyncio.ensure_future(
            _safe_emit(
                self._client_getter, _TRANSCRIPTION_DELTA_METHOD, {"error": message}
            )
        )


class _NarratorListener(NarratorManagerListener):
    """Bridges NarratorManager state changes to ACP ext_notification."""

    def __init__(self, client_getter: Callable[[], Client]) -> None:
        self._client_getter = client_getter
        self._last_state: NarratorState = NarratorState.IDLE
        self._reached_speaking = False
        self._canceling = False

    def mark_canceling(self) -> None:
        self._canceling = True

    def on_narrator_state_change(self, state: NarratorState) -> None:
        if state == self._last_state:
            return
        self._last_state = state

        if state == NarratorState.SUMMARIZING:
            self._reached_speaking = False
            self._canceling = False
            asyncio.ensure_future(
                _safe_emit(self._client_getter, _NARRATION_PREP_METHOD)
            )
        elif state == NarratorState.SPEAKING:
            self._reached_speaking = True
        elif state == NarratorState.IDLE:
            if self._canceling:
                self._canceling = False
                asyncio.ensure_future(
                    _safe_emit(self._client_getter, _NARRATION_DONE_METHOD)
                )
            elif self._reached_speaking:
                asyncio.ensure_future(
                    _safe_emit(self._client_getter, _NARRATION_DONE_METHOD)
                )
            else:
                asyncio.ensure_future(
                    _safe_emit(
                        self._client_getter,
                        _NARRATION_ERROR_METHOD,
                        {"error": "Narration failed during preparation"},
                    )
                )


class VoiceController:
    """Facade over VoiceManager and NarratorManager for ACP.

    Lazily creates the CLI managers on first use, using the current
    session's app server resources (ConfigView, NarrationResource, telemetry).
    """

    def __init__(self, client_getter: Callable[[], Client]) -> None:
        self._client_getter = client_getter
        self._voice_manager: VoiceManagerPort | None = None
        self._narrator_manager: NarratorManagerPort | None = None
        self._voice_listener: _VoiceListener | None = None
        self._narrator_listener: _NarratorListener | None = None
        self._config_getter: Callable[[], ConfigView] | None = None
        self._narration_resource: NarrationResource | None = None
        self._telemetry: ClientTelemetry | None = None

    def _ensure_managers(
        self,
        config_getter: Callable[[], ConfigView],
        narration_resource: NarrationResource,
        telemetry: ClientTelemetry | None,
    ) -> None:
        if self._voice_manager is not None:
            return

        self._voice_listener = _VoiceListener(self._client_getter)
        self._narrator_listener = _NarratorListener(self._client_getter)

        self._voice_manager = create_default_voice_manager(
            config_getter=config_getter,
            telemetry_client=telemetry,
            request_metadata_getter=None,
        )
        self._voice_manager.add_listener(self._voice_listener)

        self._narrator_manager = create_default_narrator_manager(
            config_getter=config_getter,
            summary_generator=narration_resource,
            telemetry_client=telemetry,
            request_metadata_getter=None,
        )
        self._narrator_manager.add_listener(self._narrator_listener)

    # ── Transcription ──────────────────────────────────────────────

    async def transcribe_start(
        self,
        language: str = "en",
        # Mistral realtime transcription model auto-detects language.
        *,
        config_getter: Callable[[], ConfigView] | None = None,
        narration_resource: NarrationResource | None = None,
        telemetry: ClientTelemetry | None = None,
    ) -> dict:
        if config_getter is None or narration_resource is None:
            return {"ok": False, "error": "No active session"}

        try:
            self._ensure_managers(config_getter, narration_resource, telemetry)
        except Exception as exc:
            logger.warning("Failed to initialize voice managers", exc_info=True)
            return {"ok": False, "error": str(exc)}

        assert self._voice_manager is not None

        if self._voice_manager.transcribe_state.value != "idle":
            return {"ok": False, "error": "Transcription already in progress"}

        try:
            self._voice_manager.start_recording()
            return {"ok": True}
        except Exception as exc:
            logger.warning("Failed to start recording", exc_info=True)
            return {"ok": False, "error": str(exc)}

    async def transcribe_stop(self) -> dict:
        if self._voice_manager is None:
            return {"ok": True, "text": ""}

        try:
            await self._voice_manager.stop_recording()
            return {"ok": True, "text": ""}
        except Exception as exc:
            logger.warning("Failed to stop recording", exc_info=True)
            return {"ok": False, "error": str(exc)}

    async def transcribe_cancel(self) -> dict:
        if self._voice_manager is None:
            return {"ok": True}

        try:
            self._voice_manager.cancel_recording()
            return {"ok": True}
        except Exception as exc:
            logger.warning("Failed to cancel recording", exc_info=True)
            return {"ok": False, "error": str(exc)}

    # ── Narration ───────────────────────────────────────────────────

    async def narrate(
        self,
        user_message: str,
        assistant_text: str,
        *,
        config_getter: Callable[[], ConfigView] | None = None,
        narration_resource: NarrationResource | None = None,
        telemetry: ClientTelemetry | None = None,
    ) -> dict:
        if config_getter is None or narration_resource is None:
            return {"ok": False, "error": "No active session"}

        try:
            self._ensure_managers(config_getter, narration_resource, telemetry)
        except Exception as exc:
            logger.warning("Failed to initialize voice managers", exc_info=True)
            return {"ok": False, "error": str(exc)}

        assert self._narrator_manager is not None

        # Cancel any in-flight playback before starting a new one. Without
        # this, back-to-back turns raise AlreadyPlayingError and the listener
        # reports a prep failure while the old clip keeps playing.
        self._narrator_manager.cancel()

        try:
            self._narrator_manager.on_turn_start(user_message)
            self._narrator_manager.on_assistant_text(assistant_text)
            self._narrator_manager.on_turn_end()
            return {"ok": True}
        except Exception as exc:
            logger.warning("Narration failed", exc_info=True)
            return {"ok": False, "error": str(exc)}

    async def narrate_cancel(self) -> dict:
        if self._narrator_manager is not None:
            if self._narrator_listener is not None:
                self._narrator_listener.mark_canceling()
            try:
                self._narrator_manager.cancel()
            except Exception as exc:
                logger.warning("Failed to cancel narration", exc_info=True)
                return {"ok": False, "error": str(exc)}
        # Emit done so the UI clears its narrating state. The NarratorManager
        # will also settle to IDLE, but the listener's mark_canceling ensures
        # that transition emits done, not error.
        asyncio.ensure_future(_safe_emit(self._client_getter, _NARRATION_DONE_METHOD))
        return {"ok": True}

    async def close(self) -> None:
        if self._voice_manager is not None:
            await self._voice_manager.close()
        if self._narrator_manager is not None:
            await self._narrator_manager.close()
