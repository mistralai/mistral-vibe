from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from contextlib import suppress
from http import HTTPStatus
import json
import logging
import time

import httpx
from mistralai.client.errors import MistralError

from mistralai_vibe_local_harness.protocol import (
    JsonObject,
    RustCompletionFailedEvent,
    RustCompletionSucceededEvent,
    RustImageContentBlock,
    RustLLMCallAction,
    RustMessage,
    RustProtocolError,
    RustTextContentBlock,
    RustToolDefinition,
    RustUserMessage,
)
from mistralai_vibe_local_harness.vibe._credentials import (
    ProviderAuthRequired,
    ProviderCredentialResult,
    ProviderRejectionReason,
)
from mistralai_vibe_local_harness.vibe._runtime_config import (
    CompletionDelta,
    CompletionDeltaSink,
    LocalModelRoute,
    LocalRuntimeAdapterConfig,
    ProviderRetry,
    ProviderStreamDelta,
    RequestSentTelemetry,
)
from mistralai_vibe_local_harness.vibe.adapters._correlation import (
    CORRELATION_ID_HEADER,
)
from mistralai_vibe_local_harness.vibe.adapters.generic import (
    execute_generic_completion,
)
from mistralai_vibe_local_harness.vibe.adapters.mistral import (
    execute_mistral_completion,
)

logger = logging.getLogger(__name__)

# Stable user-facing message for a rejected credential. The Host authentication
# contract asserts this value character-for-character.
INVALID_API_KEY_MESSAGE = "Invalid API key. Please check your API key and try again."


def invalid_api_key_message(api_key_source: str | None) -> str:
    """The rejection sentence, naming where the key came from.

    ``api_key_source`` is the Host's description of the origin, such as
    "env var MISTRAL_API_KEY" or "the keyring": which places a credential can
    come from is the Host's knowledge, not the Harness's.
    """
    if not api_key_source:
        return INVALID_API_KEY_MESSAGE
    return f"Invalid API key (from {api_key_source}). Please check your API key and try again."


_REJECTION_REASONS: dict[int, ProviderRejectionReason] = {
    HTTPStatus.UNAUTHORIZED: "http_unauthorized",
    HTTPStatus.FORBIDDEN: "http_forbidden",
}

# Publication window for provisional completion fragments. The first fragment
# goes out immediately so visible output appears as soon as the model starts
# answering; later fragments coalesce into one sink call per window so the
# projection's cost stays flat no matter how fast the provider chunks.
_PROVISIONAL_FLUSH_INTERVAL_S = 0.075


class _ProvisionalDeltaEmitter:
    """Coalesce provider chunks into provisional-publication deltas.

    The sink runs while the provider stream is live, so publication is
    best-effort: a sink error is logged and the fragment dropped, never
    surfaced to the model or the turn as a provider failure.
    """

    def __init__(self, action_id: str, turn_id: str, sink: CompletionDeltaSink) -> None:
        self._action_id = action_id
        self._turn_id = turn_id
        self._sink = sink
        self._text = ""
        self._reasoning = ""
        self._last_flush = 0.0
        self._published = False
        # Two producers reach the sink: the provider's chunks and the pump.
        # Fragments append to the entry the projector already holds, so their
        # order is the answer's order -- serialise here rather than leaning on
        # the fairness of a lock two layers down in the runtime.
        self._lock = asyncio.Lock()
        self._pump = asyncio.create_task(self._drain_buffer())

    async def observe(self, delta: ProviderStreamDelta) -> None:
        self._text += delta.text
        self._reasoning += delta.reasoning
        now = time.monotonic()
        if self._published and now - self._last_flush < _PROVISIONAL_FLUSH_INTERVAL_S:
            return
        await self._flush(now)

    async def _drain_buffer(self) -> None:
        """Publish a buffered tail the stream stopped feeding.

        ``observe`` only flushes when a chunk arrives, so without this the last
        fragment of a stalled or finished stream would sit unpublished for as
        long as the stall lasts -- and be lost outright if the user interrupts
        meanwhile. The window bounds staleness instead of the chunk rate doing it.
        """
        while True:
            await asyncio.sleep(_PROVISIONAL_FLUSH_INTERVAL_S)
            if self._text or self._reasoning:
                await self._flush(time.monotonic())

    async def restart(self) -> None:
        """Discard the failed attempt's projected output before a retry."""
        async with self._lock:
            if not self._published and not (self._text or self._reasoning):
                return
            self._text = ""
            self._reasoning = ""
            self._published = False
            self._last_flush = time.monotonic()
            await self._emit(
                CompletionDelta(
                    action_id=self._action_id, turn_id=self._turn_id, restart=True
                )
            )

    async def aclose(self) -> None:
        """Stop the pump and publish whatever it had not yet sent.

        On success the flush is redundant -- the committed entry supersedes the
        projection moments later. On failure or on the interrupt that cancelled
        the stream, it is what keeps the tail of a half-finished answer on
        screen, so the turn settles the whole of what the model produced rather
        than a truncation.
        """
        self._pump.cancel()
        with suppress(asyncio.CancelledError):
            await self._pump
        try:
            await self._flush(time.monotonic())
        finally:
            self._text = ""
            self._reasoning = ""

    async def _flush(self, now: float) -> None:
        async with self._lock:
            self._last_flush = now
            text, reasoning = self._text, self._reasoning
            if not (text or reasoning):
                return
            # The opening fragment resets the action's projection: a recovered or
            # re-dispatched completion carries the same action id, and its stream
            # restates content from the beginning rather than continuing where the
            # abandoned attempt stopped.
            restart = not self._published
            await self._emit(
                CompletionDelta(
                    action_id=self._action_id,
                    turn_id=self._turn_id,
                    text=text,
                    reasoning=reasoning,
                    restart=restart,
                )
            )
            # Dropped from the buffer only once the sink has taken it. A cancel
            # mid-send therefore leaves the fragment for ``aclose`` to resend
            # rather than losing it, and cannot hand back one the projector
            # already applied. ``observe`` appends while the sink runs, so trim
            # the prefix that went out instead of clearing.
            self._text = self._text[len(text) :]
            self._reasoning = self._reasoning[len(reasoning) :]
            self._published = True

    async def _emit(self, delta: CompletionDelta) -> None:
        try:
            await self._sink(delta)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Could not project provisional completion content", exc_info=True
            )


async def execute_completion(
    action: RustLLMCallAction,
    messages: list[RustMessage],
    tools: list[RustToolDefinition],
    config: LocalRuntimeAdapterConfig,
    retry_sink: Callable[[str, ProviderRetry | None], Awaitable[None]] | None = None,
    delta_sink: CompletionDeltaSink | None = None,
) -> RustCompletionSucceededEvent | RustCompletionFailedEvent:
    turn_id = action.turn_id

    route = (
        config.compaction_model
        if action.purpose == "compaction"
        else config.active_model
    )
    image_error = _validate_image_support(messages, route)
    if image_error is not None:
        return RustCompletionFailedEvent(action_id=action.action_id, error=image_error)
    try:
        credential: ProviderCredentialResult = await config.credentials.resolve()
    except Exception as exc:
        # A resolver is not allowed to fail the turn with an untyped error.
        return _stream_failed_event(action, exc, provider=config.provider, route=route)
    if isinstance(credential, ProviderAuthRequired):
        return _auth_required_event(action, credential)

    # Emit before the provider call and outside the try so a sink error is not
    # misclassified as a provider stream failure.
    _emit_request_sent(action, messages, config, route)

    # Built last: the emitter owns a publication task that only ``aclose`` stops,
    # so no return may sit between here and the block whose ``finally`` closes it.
    # ``CompletionDelta.turn_id`` says why a turn is required, and the protocol
    # already guarantees agent calls carry one; this narrows rather than checks.
    emitter = (
        _ProvisionalDeltaEmitter(action.action_id, turn_id, delta_sink)
        if delta_sink is not None and action.purpose == "agent" and turn_id is not None
        else None
    )

    async def on_retry(retry: ProviderRetry | None) -> None:
        # Only a real retry restarts the projection; a ``None`` payload is the
        # "recovered" signal the adapters send once an attempt succeeds.
        if retry is not None and emitter is not None:
            await emitter.restart()
        if retry_sink is None or turn_id is None:
            return
        try:
            await retry_sink(turn_id, retry)
        except Exception:
            logger.warning("Could not report provider retry", exc_info=True)

    retry_observer = (
        on_retry
        if (retry_sink is not None and turn_id is not None) or emitter is not None
        else None
    )
    on_delta = emitter.observe if emitter is not None else None
    try:
        if config.backend == "mistral":
            result = await execute_mistral_completion(
                messages,
                tools,
                config,
                credential,
                route,
                stream=action.purpose == "agent",
                metadata=(
                    config.completion_metadata(action.purpose, action.iteration)
                    if config.completion_metadata is not None
                    else None
                ),
                on_retry=retry_observer,
                on_delta=on_delta,
            )
        elif config.backend == "generic":
            result = await execute_generic_completion(
                messages,
                tools,
                config,
                credential,
                route=route,
                on_retry=retry_observer,
                on_delta=on_delta,
            )
        else:
            raise ValueError(f"Unsupported completion backend: {config.backend}")
    except Exception as exc:
        rejection = _REJECTION_REASONS.get(_provider_status(exc) or 0)
        if rejection is None:
            return _stream_failed_event(
                action, exc, provider=config.provider, route=route
            )
        # Tell the credential's owner what the borrower observed, so the
        # material just refused is not re-sent on the next turn.
        await config.credentials.reject(
            observed_revision=credential.revision, reason=rejection
        )
        return _unauthorized_event(
            action,
            rejection,
            credential.api_key_source,
            details=_failure_details(action, config.provider, route, exc),
        )
    finally:
        if emitter is not None:
            await emitter.aclose()
    return RustCompletionSucceededEvent(action_id=action.action_id, result=result)


def _validate_image_support(
    messages: list[RustMessage], route: LocalModelRoute
) -> RustProtocolError | None:
    if route.supports_images:
        return None

    for message in messages:
        for block in message.content:
            if isinstance(block, RustImageContentBlock):
                return RustProtocolError(
                    code="images_not_supported",
                    message=(
                        f"Model {route.model} does not support images, but the completion "
                        "input still contains an image"
                    ),
                    retryable=False,
                    details={"model": route.model},
                )
    return None


def _text_length(content: Iterable[object]) -> int:
    """Sum text-block characters, ignoring non-text parts (images, tool calls).

    Harness messages carry typed content blocks, so only ``text`` blocks count.
    """
    return sum(
        len(block.text) for block in content if isinstance(block, RustTextContentBlock)
    )


def _emit_request_sent(
    action: RustLLMCallAction,
    messages: list[RustMessage],
    config: LocalRuntimeAdapterConfig,
    route: LocalModelRoute,
) -> None:
    """Report the request's shape to the Host's per-completion telemetry sink.

    Counts text characters across the whole context and uses the last user
    message as the prompt size.
    """
    sink = config.request_sent_sink
    if sink is None:
        return
    nb_context_chars = 0
    nb_prompt_chars = 0
    for message in messages:
        chars = _text_length(message.content)
        nb_context_chars += chars
        if isinstance(message, RustUserMessage):
            nb_prompt_chars = chars
    sink(
        RequestSentTelemetry(
            model=route.model,
            purpose=action.purpose,
            iteration=action.iteration,
            nb_context_chars=nb_context_chars,
            nb_context_messages=len(messages),
            nb_prompt_chars=nb_prompt_chars,
        )
    )


def _provider_status(exc: BaseException) -> int | None:
    """Read an HTTP status off a provider exception, structurally.

    The Mistral SDK, HTTPX, and any future adapter each expose one of
    ``status_code`` or ``status``, on the exception or on its ``response``.
    Importing any one of them here would pin the Harness to a single client.
    """
    for source in (exc, getattr(exc, "response", None)):
        if source is None:
            continue
        for attribute in ("status_code", "status"):
            value = getattr(source, attribute, None)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def _auth_required_event(
    action: RustLLMCallAction, credential: ProviderAuthRequired
) -> RustCompletionFailedEvent:
    return RustCompletionFailedEvent(
        action_id=action.action_id,
        error=RustProtocolError(
            code="model_unauthorized",
            message=credential.message,
            retryable=False,
            details={"provider": credential.provider, "reason": credential.reason},
        ),
    )


def _unauthorized_event(
    action: RustLLMCallAction,
    reason: ProviderRejectionReason,
    api_key_source: str | None,
    *,
    details: JsonObject,
) -> RustCompletionFailedEvent:
    # Only a 401 says the credential itself was refused. A 403 is a permission
    # problem, so naming the variable the key came from would misdirect.
    refused_key = api_key_source if reason == "http_unauthorized" else None
    return RustCompletionFailedEvent(
        action_id=action.action_id,
        error=RustProtocolError(
            code="model_unauthorized",
            message=invalid_api_key_message(refused_key),
            retryable=False,
            details=details | {"reason": reason},
        ),
    )


def _stream_failed_event(
    action: RustLLMCallAction,
    exc: BaseException,
    *,
    provider: str,
    route: LocalModelRoute,
) -> RustCompletionFailedEvent:
    return RustCompletionFailedEvent(
        action_id=action.action_id,
        error=RustProtocolError(
            code="model_stream_failed",
            message=_failure_message(exc),
            retryable=False,
            details=_failure_details(action, provider, route, exc),
        ),
    )


def _failure_message(exc: BaseException) -> str:
    """The failure sentence, carrying the provider's own explanation.

    A status error renders as the status line and the URL, which says the
    request failed but not why. The body usually says why, and that is the part
    a user can act on.
    """
    rendered = str(exc)
    detail = _provider_message(exc)
    if detail is None or detail in rendered:
        return rendered
    return f"{rendered}: {detail}"


def _provider_message(exc: BaseException) -> str | None:
    """The message a provider put in an error response body.

    The OpenAI-compatible ``error.message`` first, then its common variants.
    """
    body = _response_body(exc)
    if body is None:
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    for candidate in (
        error.get("message") if isinstance(error, dict) else None,
        error if isinstance(error, str) else None,
        payload.get("message"),
        payload.get("detail"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _response_body(exc: BaseException) -> str | None:
    if isinstance(exc, MistralError):
        return exc.body
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    try:
        return exc.response.text
    except httpx.ResponseNotRead:
        # Reading here would block on a connection the failure already abandoned.
        return None


def _failure_details(
    action: RustLLMCallAction, provider: str, route: LocalModelRoute, exc: BaseException
) -> JsonObject:
    details: JsonObject = {
        "provider": provider,
        "model": route.model,
        "purpose": action.purpose,
        "exceptionType": type(exc).__name__,
    }
    if (status := _provider_status(exc)) is not None:
        details["httpStatus"] = status

    if isinstance(exc, MistralError):
        headers = exc.headers
    elif isinstance(exc, httpx.HTTPStatusError):
        headers = exc.response.headers
    else:
        return details
    # Only the failed response identifies this request; a previous response's
    # correlation ID belongs to a different completion or retry attempt.
    if correlation_id := headers.get(CORRELATION_ID_HEADER):
        details["correlationId"] = correlation_id
    return details


__all__ = ["INVALID_API_KEY_MESSAGE", "execute_completion", "invalid_api_key_message"]
