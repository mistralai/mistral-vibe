"""The ``voice/*`` API: dictation and narration over the CLI's audio managers.

The app server owns the feature (ADR 0018): the audio I/O lives where the
microphone and speakers are, which for every Vibe client is the process that
hosts the app server. Clients only start, stop and cancel, and render the
pushed lifecycle notifications.

This module contains zero audio code — no miniaudio, no WebSocket, no TTS. It
wires the CLI managers (VoiceManager for dictation, NarratorManager for
narration) behind the session-scoped ``voice/*`` methods, translating their
listener callbacks into ``voice/*`` notifications. The wire names are the ones
the ACP subprocess used, so a client that spoke ``voice/*`` over ACP speaks the
same method names here.

The controller takes the collaborators the CLI managers already consume — a
config getter, a ``TurnSummaryGenerator``, a ``ClientTelemetry``, the session
check and a notification sink — and nothing else (ADR 0018: inject only used
capabilities).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

from vibe.app_server._dispatch import DispatchResult, method_not_found
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server.protocol import (
    VOICE_NARRATION_DONE_METHOD,
    VOICE_NARRATION_ERROR_METHOD,
    VOICE_NARRATION_PREP_METHOD,
    VOICE_TRANSCRIPTION_DELTA_METHOD,
    VoiceNarrateCancelParams,
    VoiceNarrateCancelResponse,
    VoiceNarrateParams,
    VoiceNarrateResponse,
    VoiceNarrationDoneParams,
    VoiceNarrationErrorParams,
    VoiceNarrationPrepParams,
    VoiceTranscribeCancelParams,
    VoiceTranscribeCancelResponse,
    VoiceTranscribeStartParams,
    VoiceTranscribeStartResponse,
    VoiceTranscribeStopParams,
    VoiceTranscribeStopResponse,
    VoiceTranscriptionDeltaParams,
    _VoiceCommandParams,
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
    from vibe.app_server.config import ConfigView
    from vibe.app_server.telemetry_port import ClientTelemetry
    from vibe.cli.turn_summary.port import TurnSummaryGenerator


__all__ = ["VOICE_METHODS", "VoiceController", "VoiceNotifier"]

VOICE_METHODS = frozenset({
    "voice/transcribeStart",
    "voice/transcribeStop",
    "voice/transcribeCancel",
    "voice/narrate",
    "voice/narrateCancel",
})

type VoiceNotifier = Callable[[str, ProtocolModel], Awaitable[None]]

_ParamsT = TypeVar("_ParamsT", bound=_VoiceCommandParams)


async def _emit(notify: VoiceNotifier, method: str, params: ProtocolModel) -> None:
    """Push one notification, swallowing all errors: a failed push must never
    take down the audio path that produced it.
    """
    try:
        await notify(method, params)
    except Exception:
        logger.warning("Failed to emit %s", method, exc_info=True)


class _VoiceListener(VoiceManagerListener):
    """Bridges VoiceManager callbacks to voice notifications."""

    def __init__(self, notify: VoiceNotifier) -> None:
        self._notify = notify

    def on_transcribe_text(self, text: str) -> None:
        asyncio.ensure_future(
            _emit(
                self._notify,
                VOICE_TRANSCRIPTION_DELTA_METHOD,
                VoiceTranscriptionDeltaParams(text=text),
            )
        )

    def on_transcribe_error(self, message: str) -> None:
        asyncio.ensure_future(
            _emit(
                self._notify,
                VOICE_TRANSCRIPTION_DELTA_METHOD,
                VoiceTranscriptionDeltaParams(error=message),
            )
        )


class _NarratorListener(NarratorManagerListener):
    """Bridges NarratorManager state changes to voice notifications."""

    def __init__(self, notify: VoiceNotifier) -> None:
        self._notify = notify
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
                _emit(
                    self._notify,
                    VOICE_NARRATION_PREP_METHOD,
                    VoiceNarrationPrepParams(),
                )
            )
        elif state == NarratorState.SPEAKING:
            self._reached_speaking = True
        elif state == NarratorState.IDLE:
            if self._canceling or self._reached_speaking:
                self._canceling = False
                asyncio.ensure_future(
                    _emit(
                        self._notify,
                        VOICE_NARRATION_DONE_METHOD,
                        VoiceNarrationDoneParams(),
                    )
                )
            else:
                asyncio.ensure_future(
                    _emit(
                        self._notify,
                        VOICE_NARRATION_ERROR_METHOD,
                        VoiceNarrationErrorParams(
                            error="Narration failed during preparation"
                        ),
                    )
                )


class VoiceController:
    """The ``voice/*`` methods over VoiceManager and NarratorManager.

    Lazily creates the CLI managers on first use from the injected session
    collaborators, and pushes their listener callbacks to the client as
    ``voice/*`` notifications.
    """

    def __init__(
        self,
        *,
        require_session: Callable[[str], None],
        config_getter: Callable[[], ConfigView],
        summary_generator: TurnSummaryGenerator,
        telemetry: ClientTelemetry | None,
        notify: VoiceNotifier,
    ) -> None:
        self._require_session = require_session
        self._config_getter = config_getter
        self._summary_generator = summary_generator
        self._telemetry = telemetry
        self._notify = notify
        self._voice_manager: VoiceManagerPort | None = None
        self._narrator_manager: NarratorManagerPort | None = None
        self._voice_listener: _VoiceListener | None = None
        self._narrator_listener: _NarratorListener | None = None

    def _command(self, model: type[_ParamsT], raw_params: dict[str, Any]) -> _ParamsT:
        params = validate_wire(model, raw_params)
        self._require_session(params.session_id)
        return params

    def _ensure_managers(self) -> None:
        if self._voice_manager is not None:
            return

        # A local import keeps ``vibe.cli``'s dependency chain out of the app
        # server's import graph until audio is actually used.
        from vibe.cli.lazy_audio_managers import (
            create_default_narrator_manager,
            create_default_voice_manager,
        )

        self._voice_listener = _VoiceListener(self._notify)
        self._narrator_listener = _NarratorListener(self._notify)

        self._voice_manager = create_default_voice_manager(
            config_getter=self._config_getter,
            telemetry_client=self._telemetry,
            request_metadata_getter=None,
        )
        self._voice_manager.add_listener(self._voice_listener)

        self._narrator_manager = create_default_narrator_manager(
            config_getter=self._config_getter,
            summary_generator=self._summary_generator,
            telemetry_client=self._telemetry,
            request_metadata_getter=None,
        )
        self._narrator_manager.add_listener(self._narrator_listener)

    # -- Transcription ──────────────────────────────────────────────

    async def transcribe_start(self, language: str = "en") -> ProtocolModel:
        # ``language`` stays on the wire for the clients the ACP path served;
        # the realtime transcription model detects the language itself.
        del language

        try:
            self._ensure_managers()
        except Exception as exc:
            logger.warning("Failed to initialize voice managers", exc_info=True)
            return VoiceTranscribeStartResponse(ok=False, error=str(exc))

        assert self._voice_manager is not None

        if self._voice_manager.transcribe_state.value != "idle":
            return VoiceTranscribeStartResponse(
                ok=False, error="Transcription already in progress"
            )

        try:
            self._voice_manager.start_recording()
            return VoiceTranscribeStartResponse(ok=True)
        except Exception as exc:
            logger.warning("Failed to start recording", exc_info=True)
            return VoiceTranscribeStartResponse(ok=False, error=str(exc))

    async def transcribe_stop(self) -> ProtocolModel:
        if self._voice_manager is None:
            return VoiceTranscribeStopResponse(ok=True, text="")

        try:
            await self._voice_manager.stop_recording()
            return VoiceTranscribeStopResponse(ok=True, text="")
        except Exception as exc:
            logger.warning("Failed to stop recording", exc_info=True)
            return VoiceTranscribeStopResponse(ok=False, error=str(exc))

    async def transcribe_cancel(self) -> ProtocolModel:
        if self._voice_manager is None:
            return VoiceTranscribeCancelResponse(ok=True)

        try:
            self._voice_manager.cancel_recording()
            return VoiceTranscribeCancelResponse(ok=True)
        except Exception as exc:
            logger.warning("Failed to cancel recording", exc_info=True)
            return VoiceTranscribeCancelResponse(ok=False, error=str(exc))

    # -- Narration ──────────────────────────────────────────────────

    async def narrate(self, user_message: str, assistant_text: str) -> ProtocolModel:
        try:
            self._ensure_managers()
        except Exception as exc:
            logger.warning("Failed to initialize voice managers", exc_info=True)
            return VoiceNarrateResponse(ok=False, error=str(exc))

        assert self._narrator_manager is not None

        # Cancel any in-flight playback before starting a new one. Without
        # this, back-to-back turns raise AlreadyPlayingError and the listener
        # reports a prep failure while the old clip keeps playing.
        self._narrator_manager.cancel()

        try:
            self._narrator_manager.on_turn_start(user_message)
            self._narrator_manager.on_assistant_text(assistant_text)
            self._narrator_manager.on_turn_end()
            return VoiceNarrateResponse(ok=True)
        except Exception as exc:
            logger.warning("Narration failed", exc_info=True)
            return VoiceNarrateResponse(ok=False, error=str(exc))

    async def narrate_cancel(self) -> ProtocolModel:
        if self._narrator_manager is not None:
            if self._narrator_listener is not None:
                self._narrator_listener.mark_canceling()
            try:
                self._narrator_manager.cancel()
            except Exception as exc:
                logger.warning("Failed to cancel narration", exc_info=True)
                return VoiceNarrateCancelResponse(ok=False, error=str(exc))
        # Emit done so the UI clears its narrating state. The NarratorManager
        # will also settle to IDLE, but the listener's mark_canceling ensures
        # that transition emits done, not error.
        asyncio.ensure_future(
            _emit(self._notify, VOICE_NARRATION_DONE_METHOD, VoiceNarrationDoneParams())
        )
        return VoiceNarrateCancelResponse(ok=True)

    async def close(self) -> None:
        if self._voice_manager is not None:
            await self._voice_manager.close()
        if self._narrator_manager is not None:
            await self._narrator_manager.close()

    # -- Routing ────────────────────────────────────────────────────

    async def dispatch(self, method: str, raw_params: dict[str, Any]) -> DispatchResult:
        match method:
            case "voice/transcribeStart":
                params = self._command(VoiceTranscribeStartParams, raw_params)
                return DispatchResult(await self.transcribe_start(params.language))
            case "voice/transcribeStop":
                self._command(VoiceTranscribeStopParams, raw_params)
                return DispatchResult(await self.transcribe_stop())
            case "voice/transcribeCancel":
                self._command(VoiceTranscribeCancelParams, raw_params)
                return DispatchResult(await self.transcribe_cancel())
            case "voice/narrate":
                params = self._command(VoiceNarrateParams, raw_params)
                return DispatchResult(
                    await self.narrate(params.user_message, params.assistant_text)
                )
            case "voice/narrateCancel":
                self._command(VoiceNarrateCancelParams, raw_params)
                return DispatchResult(await self.narrate_cancel())
            case _:
                raise method_not_found(method)
