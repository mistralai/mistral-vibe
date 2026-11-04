"""The commands the adapter sends through the Session API.

``SessionApiCommands`` owns the ``LocalSessionApiTransport`` of one root
Session and sends the commands the Session API defines: Turn start, steer and
interrupt, callback answers, compaction and model switches. Each reply has the
shape of the Runtime's own reply, so the adapter reads both paths the same way.

Some commands go to the Runtime directly, because the API cannot carry them:

- a Turn start or steer with Vibe display content or the ``injected`` flag;
- a settings change other than a model switch, and a model switch applied
  while a Turn is reserved.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
import logging
from typing import Any, assert_never

from mistralai_vibe_local_harness.protocol import RustHarnessConfig
from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    ContentBlock,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    ResourceLinkContentBlock,
    TextContentBlock,
)
from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    CallbackResultParams as ApiCallbackResultParams,
    CallbackResultResponse as ApiCallbackResultResponse,
    ConfigReadParams,
    ConfigReadResponse,
    ConfigWriteParams,
    ConfigWriteResponse,
    SessionCompactParams as ApiCompactParams,
    SessionCompactResponse as ApiCompactResponse,
    SessionReadParams as ApiReadParams,
    SessionReadResponse as ApiReadResponse,
    TurnInterruptParams as ApiInterruptParams,
    TurnInterruptResponse as ApiInterruptResponse,
    TurnStartParams as ApiTurnStartParams,
    TurnStartResponse as ApiTurnStartResponse,
    TurnSteerParams as ApiTurnSteerParams,
    TurnSteerResponse as ApiTurnSteerResponse,
)
from mistralai_vibe_local_harness.session_api.app_server.session.transport import (
    SessionCallbackSettledError,
    SessionIdempotencyConflictError,
    SessionTurnConflictError,
)
from mistralai_vibe_local_harness.vibe import (
    HarnessSessionError,
    HarnessSessionNotFoundError,
    HarnessStaleTurnError,
    HarnessTurnQueueIdempotencyConflictError,
    SessionConfig,
    UnifiedHarnessSessionBackend,
)
from mistralai_vibe_local_harness.vibe._runtime_config import LocalRuntimeAdapterConfig
from mistralai_vibe_local_harness.vibe._session_api_config import (
    COMPLETION_FIELDS_APPLIED,
    AppliedCompletion,
    SessionConfigRejectedError,
)
from mistralai_vibe_local_harness.vibe._session_api_transport import (
    ChildSessions,
    LocalSessionApiTransport,
    session_api_view_of,
)
from vibe.app_server._session_commands import (
    Accepted,
    CommandResult,
    SessionCommands,
    StartedTurn,
    accepted,
    turn_started,
)
from vibe.app_server._time import now_ms
from vibe.app_server.models import (
    ContentBlock as VibeContentBlock,
    ImageContentBlock as VibeImageContentBlock,
    InlineImageSource,
    TextContentBlock as VibeTextContentBlock,
)
from vibe.app_server.protocol import (
    CallbackResultParams,
    TurnInterruptParams,
    TurnStartParams,
    TurnSteerParams,
)
from vibe.user_content import UserBlobResource, UserResourceLink, UserTextResource

logger = logging.getLogger(__name__)


# A Session API reply names an opaque API event ID, which Vibe clients cannot
# use, so the adapter reports its own last event ID instead.
_ACCEPTED = CommandResult(response=Accepted(accepted=True, last_event_id=None))


class SessionApiCommands(SessionCommands):
    """The Session API commands of one root Session and its subagents."""

    def __init__(
        self,
        session: UnifiedHarnessSessionBackend,
        children: ChildSessions | None = None,
    ) -> None:
        self._session = session
        self._children = children
        self.transport = LocalSessionApiTransport(
            session, session_api_view_of(session), children=children
        )
        self._opened = False
        self._open_lock = asyncio.Lock()

    async def ensure_open(self) -> None:
        async with self._open_lock:
            if self._opened:
                return
            await self.transport.open()
            self._opened = True

    async def close(self) -> None:
        if self._opened:
            await self.transport.close()
            self._opened = False

    async def start_turn(self, params: TurnStartParams) -> CommandResult[StartedTurn]:
        if params.user_display_content is not None or params.injected:
            # The API's Turn start has no field for Vibe display content.
            return turn_started(await self._session.start_turn(params))
        await self.ensure_open()
        async with _runtime_errors(self._active_turn_id):
            response = await self.transport.request(
                "app_server/session/turn/start",
                ApiTurnStartParams(
                    idempotency_key=params.idempotency_key,
                    session_id=params.session_id,
                    message_entry_id=params.client_user_message_id,
                    message=api_blocks(params.message),
                ),
                ApiTurnStartResponse,
            )
        return CommandResult(
            response=StartedTurn(
                id=response.turn.id,
                session_id=params.session_id,
                # The Session API's TurnState may omit it, for example while a
                # runtime's pre-turn Hook is still deciding.
                started_at=response.turn.started_at or now_ms(),
            )
        )

    async def steer_turn(self, params: TurnSteerParams) -> CommandResult[Accepted]:
        if params.user_display_content is not None:
            # The API's steer has no field for Vibe display content.
            return accepted(await self._session.steer_turn(params))
        await self.ensure_open()
        async with _runtime_errors(self._active_turn_id):
            await self.transport.request(
                "app_server/session/turn/steer",
                ApiTurnSteerParams(
                    idempotency_key=params.idempotency_key,
                    session_id=params.session_id,
                    expected_turn_id=params.expected_turn_id,
                    message_entry_id=params.client_user_message_id,
                    message=api_blocks(params.message),
                ),
                ApiTurnSteerResponse,
            )
        return _ACCEPTED

    async def interrupt_turn(
        self, params: TurnInterruptParams
    ) -> CommandResult[Accepted]:
        await self.ensure_open()
        async with _runtime_errors(self._active_turn_id):
            await self.transport.request(
                "app_server/session/turn/interrupt",
                ApiInterruptParams(
                    session_id=params.session_id,
                    expected_turn_id=params.expected_turn_id,
                ),
                ApiInterruptResponse,
            )
        return _ACCEPTED

    async def respond_to_callback(
        self, params: CallbackResultParams
    ) -> CommandResult[Accepted]:
        """Answer a callback of this Session or of one of its subagents."""
        self._require_known_session(params.session_id)
        await self.ensure_open()
        async with _runtime_errors(self._active_turn_id):
            await self.transport.request(
                "app_server/session/callback/result",
                ApiCallbackResultParams.model_validate({
                    "sessionId": params.session_id,
                    "result": params.result.model_dump(mode="json", by_alias=True),
                }),
                ApiCallbackResultResponse,
            )
        return _ACCEPTED

    async def compact(self, instructions: str | None) -> ApiCompactResponse:
        await self.ensure_open()
        return await self.transport.request(
            "app_server/session/compact",
            ApiCompactParams(
                session_id=self._session.session_id, instructions=instructions
            ),
            ApiCompactResponse,
        )

    async def apply_config(
        self, config: SessionConfig, *, allow_reserved_turn: bool = False
    ) -> None:
        """Apply Vibe's settings: a model switch goes through ``config/write``.

        Any other change goes to the Runtime directly, as does one applied past
        a reserved Turn, because ``config/write`` needs an idle Session.
        """
        if (
            not allow_reserved_turn
            and not self._session.ephemeral
            and _is_model_switch(
                self._session.core_config, self._session.adapter_config, config
            )
        ):
            try:
                await self._write_model(config)
            except SessionConfigRejectedError:
                logger.info("config/write refused a model switch; pushing directly")
            else:
                return
        await self._session.apply_config(
            config, allow_reserved_turn=allow_reserved_turn
        )

    async def _write_model(self, config: SessionConfig) -> None:
        """Write the model, its settings and its resolved limits to ``completion``."""
        await self.ensure_open()
        current = (
            await self.transport.request(
                "app_server/session/config/read",
                ConfigReadParams(session_id=self._session.session_id),
                ConfigReadResponse,
            )
        ).config
        facts = config.core.settings.context.model_facts
        # The write is the whole configuration: a key the new model leaves unset,
        # such as ``top_p``, must not keep the previous model's value. The
        # provider type is not the model's, and a switch keeps it.
        kept = {
            key: value
            for key, value in current.completion.items()
            if key == "type" or key not in COMPLETION_FIELDS_APPLIED
        }
        await self.transport.request(
            "app_server/session/config/write",
            ConfigWriteParams(
                session_id=self._session.session_id,
                config=current.model_copy(
                    update={
                        "completion": {
                            **kept,
                            **AppliedCompletion.of(
                                config.local.active_model, facts
                            ).dump(),
                        },
                        "auto_compact_threshold": facts.compaction_threshold,
                    }
                ),
            ),
            ConfigWriteResponse,
        )

    def _require_known_session(self, session_id: str) -> None:
        """Refuse a Session that is neither this one nor one of its subagents."""
        root_id = self._session.session_id
        if session_id == root_id:
            return
        if self._children is None or not self._children.references_child(
            root_id, session_id
        ):
            raise HarnessSessionNotFoundError(session_id)

    async def _active_turn_id(self) -> str | None:
        response = await self.transport.request(
            "app_server/session/read",
            ApiReadParams(session_id=self._session.session_id),
            ApiReadResponse,
        )
        return response.state.active_turn_id


def _is_model_switch(
    core: RustHarnessConfig | None,
    local: LocalRuntimeAdapterConfig | None,
    config: SessionConfig,
) -> bool:
    """Whether ``config`` differs from the running one only by its model.

    A model's route and facts may change, and compaction may follow the model.
    The task ID, plugins and hook bindings are left out: the app server
    derives template values for them, and the Session keeps its own. So are
    the request callbacks: each derivation brings fresh ones, which the adapter
    binds to the same Session sources (``_bind_completion_sources``). So are the
    tool groups: a resident Core takes them from the Session's own MCP and
    connector snapshots, and ``apply_config`` never pushes them to it, on
    either path. Only a resident Session gets here (``ephemeral`` is checked
    first).
    """
    if core is None or local is None:
        return False
    if config.local.active_model == local.active_model:
        return False
    follows = local.compaction_model == local.active_model
    moved = replace(
        local,
        active_model=config.local.active_model,
        compaction_model=(
            config.local.active_model if follows else local.compaction_model
        ),
        affinity_id=config.local.affinity_id,
        completion_metadata=config.local.completion_metadata,
        request_sent_sink=config.local.request_sent_sink,
        model_request_sink=config.local.model_request_sink,
    )
    if config.local != moved:
        return False
    return _without_model(config.core) == _without_model(core)


def _without_model(core: RustHarnessConfig) -> dict[str, Any]:
    """``core`` without what a model switch changes or the app server templates."""
    dumped = core.model_dump(mode="json")
    dumped.pop("task_id", None)
    dumped.pop("plugins", None)
    dumped["capabilities"].pop("hook_bindings", None)
    dumped["capabilities"].pop("tool_groups", None)
    dumped["settings"]["context"].pop("model_facts", None)
    dumped["settings"]["context"].pop("compaction_model_input_modalities", None)
    return dumped


@asynccontextmanager
async def _runtime_errors(
    active_turn_id: Callable[[], Awaitable[str | None]],
) -> AsyncIterator[None]:
    """Turn the Session API's errors back into the Runtime errors the adapter maps.

    The API's Turn conflict carries no active Turn ID, so it is read back from
    the API state. The API's settled callback error does not say whether the
    callback was closed, unknown or answered differently, so the Runtime's own
    reason is taken from the error's cause.
    """
    try:
        yield
    except SessionTurnConflictError as exc:
        raise HarnessStaleTurnError(await active_turn_id()) from exc
    except SessionIdempotencyConflictError as exc:
        raise HarnessTurnQueueIdempotencyConflictError(exc.idempotency_key) from exc
    except SessionCallbackSettledError as exc:
        if isinstance(exc.__cause__, HarnessSessionError):
            raise exc.__cause__ from exc
        raise


def api_blocks(message: Sequence[VibeContentBlock]) -> list[ContentBlock]:
    """Vibe's message blocks in Session API form."""
    return [_api_block(block) for block in message]


def _api_block(block: VibeContentBlock) -> ContentBlock:
    if isinstance(block, VibeTextContentBlock):
        return TextContentBlock(text=block.text)
    if isinstance(block, VibeImageContentBlock):
        attachment = block.attachment
        source = attachment.source
        if isinstance(source, InlineImageSource):
            media = attachment.mime_type or "application/octet-stream"
            uri = f"data:{media};base64,{source.data}"
        else:
            uri = source.path
        return ImageContentBlock(uri=uri, media_type=attachment.mime_type)
    match block.resource:
        case UserResourceLink() as link:
            return ResourceLinkContentBlock(
                uri=link.uri,
                name=link.name,
                title=link.title,
                description=link.description,
                media_type=link.media_type,
                size=link.size,
            )
        case UserTextResource() as text:
            return EmbeddedResourceContentBlock(
                uri=text.uri, media_type=text.media_type, text=text.text
            )
        case UserBlobResource() as blob:
            return EmbeddedResourceContentBlock(
                uri=blob.uri, media_type=blob.media_type, blob=blob.blob
            )
        case _:
            assert_never(block.resource)


__all__ = ["SessionApiCommands", "api_blocks"]
