from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from contextlib import AsyncExitStack, suppress
from dataclasses import replace
from http import HTTPStatus
import json
import logging
import time

from mistralai_vibe_local_harness.protocol import (
    JsonObject,
    RustCompletionFailedEvent,
    RustCompletionResult,
    RustCompletionSucceededEvent,
    RustLLMCallAction,
    RustMessage,
    RustProtocolError,
    RustReasoningPart,
    RustReasoningTextContent,
    RustTextContentBlock,
    RustTokenUsage,
    RustToolDefinition,
    RustUserMessage,
)
from mistralai_vibe_local_harness.vibe._credentials import (
    ProviderAuthRequired,
    ProviderCredentialResult,
    ProviderCredentialSnapshot,
    ProviderRejectionReason,
)
from mistralai_vibe_local_harness.vibe._runtime_config import (
    CompletionDelta,
    CompletionDeltaSink,
    CompletionPurpose,
    LocalModelRoute,
    LocalRuntimeAdapterConfig,
    MalformedToolCallsTelemetry,
    ProviderRetry,
    ProviderStreamDelta,
    RequestSentTelemetry,
)
from mistralai_vibe_local_harness.vibe.adapters._correlation import (
    CORRELATION_ID_HEADER,
)
from mistralai_vibe_local_harness.vibe.adapters._provider_failure import (
    ProviderFailure,
    ProviderRefusal,
    read_provider_failure,
)
from mistralai_vibe_local_harness.vibe.adapters._tool_call_finish import (
    MalformedToolCalls,
)
from mistralai_vibe_local_harness.vibe.adapters.generic import (
    execute_generic_completion,
)
from mistralai_vibe_local_harness.vibe.adapters.mistral import (
    execute_mistral_completion,
)
from mistralai_vibe_local_harness.vibe.completion_profile import REASONING_LOWEST

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
        if not (delta.text or delta.reasoning):
            return
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


async def execute_completion(  # noqa: PLR0915 - one completion lifecycle
    action: RustLLMCallAction,
    messages: list[RustMessage],
    tools: list[RustToolDefinition],
    config: LocalRuntimeAdapterConfig,
    retry_sink: Callable[[str, ProviderRetry | None], Awaitable[None]] | None = None,
    delta_sink: CompletionDeltaSink | None = None,
) -> RustCompletionSucceededEvent | RustCompletionFailedEvent:
    turn_id = action.turn_id

    compaction = action.purpose == "compaction"
    # A standalone compaction request carries the history as a transcript and
    # runs without tools and without reasoning, so it shares nothing with the
    # in-context request that failed and cannot be answered from its cache.
    standalone = compaction and action.request == "standalone"
    route = _request_route(action, config)
    request_config = _request_config(action, config, route)
    if standalone:
        tools = []
    try:
        credential: ProviderCredentialResult = await config.credentials.resolve()
    except Exception as exc:
        # A resolver is not allowed to fail the turn with an untyped error.
        return _logged_failure(
            action,
            _stream_failed_event(action, exc, provider=config.provider, route=route),
            cause=exc,
            elapsed_s=None,
        )
    if isinstance(credential, ProviderAuthRequired):
        return _auth_required_event(action, credential)

    # Emit before the provider call and outside the try so a sink error is not
    # misclassified as a provider stream failure.
    _emit_request_sent(action, messages, config, route)
    started = time.perf_counter()

    # Built last: the emitter owns a publication task that only ``aclose`` stops,
    # so no return may sit between here and the block whose ``finally`` closes it.
    # ``CompletionDelta.turn_id`` says why a turn is required, and the protocol
    # already guarantees agent calls carry one; this narrows rather than checks.
    emitter = (
        _ProvisionalDeltaEmitter(action.action_id, turn_id, delta_sink)
        if delta_sink is not None and action.purpose == "agent" and turn_id is not None
        else None
    )

    reasoning_watch = _reasoning_watch(action, config)

    async def on_retry(retry: ProviderRetry | None) -> None:
        # Only a real retry restarts the projection; a ``None`` payload is the
        # "recovered" signal the adapters send once an attempt succeeds.
        if retry is not None and emitter is not None:
            await emitter.restart()
        if retry is not None and reasoning_watch is not None:
            reasoning_watch.restart()
        if retry_sink is None or turn_id is None:
            return
        try:
            await retry_sink(turn_id, retry)
        except Exception:
            logger.warning("Could not report provider retry", exc_info=True)

    retry_observer = (
        on_retry
        if (retry_sink is not None and turn_id is not None)
        or emitter is not None
        or reasoning_watch is not None
        else None
    )
    on_delta = (
        emitter.observe
        if emitter is not None
        else reasoning_watch.observe
        if reasoning_watch is not None
        else None
    )
    malformed = MalformedToolCalls()
    succeeded = False
    try:
        async with AsyncExitStack() as spans:
            if config.tracing is not None:
                if action.purpose == "agent":
                    await spans.enter_async_context(
                        config.tracing.agent_span(model=route.model)
                    )
                await spans.enter_async_context(
                    config.tracing.model_call_span(
                        provider_name=config.provider,
                        provider_api_style=config.api_style,
                        model=route.model,
                        streaming=config.backend == "mistral"
                        or action.purpose == "agent"
                        or reasoning_watch is not None,
                        temperature=route.temperature,
                        max_tokens=config.output_token_cap(route),
                        metadata={
                            "call_type": action.purpose,
                            "message_id": action.action_id,
                        },
                    )
                )
            if config.backend == "mistral":
                result = await execute_mistral_completion(
                    messages,
                    tools,
                    request_config,
                    credential,
                    route,
                    # Compaction streams too, although nothing is published from it: a
                    # long summary sent as one plain response transfers no bytes until
                    # it is complete, and proxies that close idle connections cut it.
                    stream=True,
                    metadata=(
                        config.completion_metadata(action.purpose, action.iteration)
                        if config.completion_metadata is not None
                        else None
                    ),
                    on_retry=retry_observer,
                    on_delta=on_delta,
                    malformed=malformed,
                )
            elif config.backend == "generic":
                result = await execute_generic_completion(
                    messages,
                    tools,
                    request_config,
                    credential,
                    route=route,
                    on_retry=retry_observer,
                    on_delta=on_delta,
                    malformed=malformed,
                )
            else:
                raise ValueError(f"Unsupported completion backend: {config.backend}")

        succeeded = True
    except CompactionReasoningLimitExceeded as stopped:
        succeeded = True
        return RustCompletionSucceededEvent(
            action_id=action.action_id,
            result=_reasoning_cut_off_result(stopped, messages, tools),
        )
    except Exception as exc:
        if isinstance(exc, ProviderRefusal) and emitter is not None:
            # Text streamed before a refusal is not an answer. Withdraw it, as a
            # retry does, instead of letting the failed turn settle it.
            await emitter.restart()
        return await _call_failure(
            action,
            exc,
            config,
            credential,
            route,
            elapsed_s=time.perf_counter() - started,
        )
    finally:
        if emitter is not None:
            await emitter.aclose()
        _report_malformed_tool_calls(
            action, config, route, malformed, succeeded=succeeded
        )
    return RustCompletionSucceededEvent(action_id=action.action_id, result=result)


async def _call_failure(
    action: RustLLMCallAction,
    exc: Exception,
    config: LocalRuntimeAdapterConfig,
    credential: ProviderCredentialSnapshot,
    route: LocalModelRoute,
    *,
    elapsed_s: float,
) -> RustCompletionFailedEvent:
    """Type a provider call's failure, and report a refused credential."""
    rejection = credential_rejection(read_provider_failure(exc))
    if rejection is None:
        failure = (
            _refusal_event(action, exc, provider=config.provider, route=route)
            if isinstance(exc, ProviderRefusal)
            else _stream_failed_event(
                action, exc, provider=config.provider, route=route
            )
        )
        return _logged_failure(action, failure, cause=exc, elapsed_s=elapsed_s)
    # Tell the credential's owner what the borrower observed, so the
    # material just refused is not re-sent on the next turn.
    await config.credentials.reject(
        observed_revision=credential.revision, reason=rejection
    )
    return _logged_failure(
        action,
        _unauthorized_event(
            action,
            rejection,
            credential.api_key_source,
            details=_failure_details(action, config.provider, route, exc),
        ),
        cause=exc,
        elapsed_s=elapsed_s,
    )


def _reasoning_watch(
    action: RustLLMCallAction, config: LocalRuntimeAdapterConfig
) -> _ReasoningWatch | None:
    """Watch an in-context compaction request's reasoning, if a limit is set.

    The standalone request is not watched: it already runs without reasoning.
    """
    limit_tokens = config.compaction_reasoning_limit_tokens
    if (
        action.purpose != "compaction"
        or action.request == "standalone"
        or limit_tokens is None
    ):
        return None
    return _ReasoningWatch(limit_tokens)


class CompactionReasoningLimitExceeded(Exception):
    """An in-context compaction request reasoned past its limit.

    Raised from the stream observer, so the provider call stops reading the
    stream and closes it. The call is not retried: it is reported as a reply
    cut off at its output limit, which Core answers with its cut-off retry, the
    standalone request without reasoning. The limit needs no failure code.
    """

    def __init__(
        self, limit_tokens: int, estimated_tokens: int, reasoning: str
    ) -> None:
        super().__init__(
            f"Compaction reasoning reached about {estimated_tokens} tokens, past "
            f"the {limit_tokens}-token limit."
        )
        self.limit_tokens = limit_tokens
        self.estimated_tokens = estimated_tokens
        self.reasoning = reasoning


def _reasoning_cut_off_result(
    stopped: CompactionReasoningLimitExceeded,
    messages: list[RustMessage],
    tools: list[RustToolDefinition],
) -> RustCompletionResult:
    """The reply a stopped compaction request is reported as.

    It holds the reasoning read so far and no answer, with the output limit as
    its finish reason. The provider sent no usage, so the usage is estimated
    from the request and the reasoning.
    """
    request_bytes = sum(len(message.model_dump_json().encode()) for message in messages)
    request_bytes += sum(len(tool.model_dump_json().encode()) for tool in tools)
    input_tokens = _approximate_tokens(request_bytes)
    return RustCompletionResult(
        parts=[
            RustReasoningPart(
                content=[RustReasoningTextContent(text=stopped.reasoning)]
            )
        ],
        finish_reason="length",
        usage=RustTokenUsage(
            input_tokens=input_tokens,
            output_tokens=stopped.estimated_tokens,
            total_tokens=input_tokens + stopped.estimated_tokens,
        ),
    )


class _ReasoningWatch:
    """Stop an in-context compaction request that keeps reasoning.

    A summary needs little reasoning. A request that reasons for tens of
    thousands of tokens is spending its output budget on the conversation
    rather than summarizing it, so it is abandoned early instead of running to
    the output cap. It stops on the delta that takes the count past the limit.

    Only a reply that is still reasoning is stopped: once answer text or a tool
    call arrives, what the model writes is kept whatever its reasoning cost, as
    in the agents_sdk runtime. The chunk that starts the answer is not counted,
    even when it carries reasoning too. Redacted reasoning carries no text, reasoning or
    tool call, so the watch never sees it: it stays on and the opaque bytes,
    which have no token meaning, are not counted.
    """

    def __init__(self, limit_tokens: int) -> None:
        self._limit_tokens = limit_tokens
        self._reasoning: list[str] = []
        self._reasoning_bytes = 0
        self._answering = False

    def restart(self) -> None:
        self._reasoning = []
        self._reasoning_bytes = 0
        self._answering = False

    async def observe(self, delta: ProviderStreamDelta) -> None:
        if self._answering:
            return
        if delta.text or delta.tool_call:
            # The answer has started: what this chunk reasons is not counted.
            self._answering = True
            return
        if delta.reasoning:
            self._reasoning.append(delta.reasoning)
        self._reasoning_bytes += len(delta.reasoning.encode())
        estimated_tokens = _approximate_tokens(self._reasoning_bytes)
        if estimated_tokens > self._limit_tokens:
            raise CompactionReasoningLimitExceeded(
                self._limit_tokens, estimated_tokens, "".join(self._reasoning)
            )


def _request_route(
    action: RustLLMCallAction, config: LocalRuntimeAdapterConfig
) -> LocalModelRoute:
    """The model route one Core request runs on.

    Compaction runs on the compaction route. The in-context request keeps the
    route's reasoning, so it shares the agent request's prefix; the standalone
    request asks for the least reasoning the model takes. That is the request's
    own setting, so the adapter still knows whether the route reasons.
    """
    if action.purpose != "compaction":
        return config.active_model
    route = config.compaction_model
    if action.request == "standalone":
        return replace(route, request_thinking=REASONING_LOWEST)
    return route


def _request_config(
    action: RustLLMCallAction, config: LocalRuntimeAdapterConfig, route: LocalModelRoute
) -> LocalRuntimeAdapterConfig:
    """The config one Core request is sent with.

    The output cap for the request's purpose becomes its ``max_tokens``, held to
    the model's known max output, to the room its window leaves at the
    compaction threshold, and to the session's own pin.
    """
    cap = (
        config.compaction_max_tokens
        if action.purpose == "compaction"
        else config.agent_max_tokens
    )
    if cap is None:
        return config
    for bound in (route.max_output_tokens, route.output_room, config.max_tokens):
        if bound is not None:
            cap = min(cap, bound)
    return replace(config, max_tokens=cap)


_APPROX_BYTES_PER_TOKEN = 4


def _approximate_tokens(byte_count: int) -> int:
    return -(-max(byte_count, 0) // _APPROX_BYTES_PER_TOKEN)


def _text_length(content: Iterable[object]) -> int:
    """Sum text-block characters, ignoring non-text parts (images, tool calls).

    Harness messages carry typed content blocks, so only ``text`` blocks count.
    """
    return sum(
        len(block.text) for block in content if isinstance(block, RustTextContentBlock)
    )


def report_request_sent(
    messages: list[RustMessage],
    config: LocalRuntimeAdapterConfig,
    route: LocalModelRoute,
    *,
    purpose: CompletionPurpose,
    iteration: int,
    turn_id: str | None = None,
) -> None:
    """Report a completion request's shape to the Host's telemetry sink.

    Counts text characters across the whole context and uses the last user
    message as the prompt size. Shared by Core-driven LLM call actions and
    Runtime-issued utility completions (the smart-approve classifier) so every
    provider call is counted the same way.
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
            purpose=purpose,
            iteration=iteration,
            nb_context_chars=nb_context_chars,
            nb_context_messages=len(messages),
            nb_prompt_chars=nb_prompt_chars,
            turn_id=turn_id,
        )
    )


def _emit_request_sent(
    action: RustLLMCallAction,
    messages: list[RustMessage],
    config: LocalRuntimeAdapterConfig,
    route: LocalModelRoute,
) -> None:
    report_request_sent(
        messages,
        config,
        route,
        purpose=action.purpose,
        iteration=action.iteration,
        turn_id=action.turn_id,
    )


def _report_malformed_tool_calls(
    action: RustLLMCallAction,
    config: LocalRuntimeAdapterConfig,
    route: LocalModelRoute,
    malformed: MalformedToolCalls,
    *,
    succeeded: bool,
) -> None:
    """Report the repairs one completion needed to the Host's telemetry sink.

    Runs once the completion has ended, so a sink error is logged rather than
    raised: it must not replace the completion's own result.
    """
    sink = config.malformed_tool_calls_sink
    if sink is None or not malformed:
        return
    try:
        sink(
            MalformedToolCallsTelemetry(
                model=route.model,
                backend=config.backend,
                api_style=config.api_style,
                purpose=action.purpose,
                iteration=action.iteration,
                reasons=tuple(sorted(malformed.reasons)),
                nb_unusable_attempts=malformed.unusable_attempts,
                nb_dropped_tool_calls=malformed.dropped_tool_calls,
                succeeded=succeeded,
                turn_id=action.turn_id,
            )
        )
    except Exception:
        logger.warning("Could not report malformed tool calls", exc_info=True)


def credential_rejection(failure: ProviderFailure) -> ProviderRejectionReason | None:
    """Why the provider refused the credential, when the failure says it did."""
    if failure.response is None:
        return None
    return _REJECTION_REASONS.get(failure.response.status)


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


def _logged_failure(
    action: RustLLMCallAction,
    event: RustCompletionFailedEvent,
    *,
    cause: BaseException,
    elapsed_s: float | None,
) -> RustCompletionFailedEvent:
    """Record a failed model call in the Host log before the turn reports it.

    The failure event only reaches the session projection; without this record
    the process log shows a request going out and nothing after it. The record
    carries the traceback of ``cause``, since some transport errors have an
    empty message and their type alone does not say where they were raised.
    """
    error = event.error
    logger.warning(
        "Model call failed code=%s purpose=%s turn_id=%s action_id=%s "
        "elapsed_s=%s details=%s message=%s",
        error.code,
        action.purpose,
        action.turn_id,
        action.action_id,
        "n/a" if elapsed_s is None else f"{elapsed_s:.1f}",
        json.dumps(error.details, sort_keys=True),
        error.message,
        exc_info=cause,
    )
    return event


def _stream_failed_event(
    action: RustLLMCallAction,
    exc: BaseException,
    *,
    provider: str,
    route: LocalModelRoute,
) -> RustCompletionFailedEvent:
    failure = read_provider_failure(exc)
    return RustCompletionFailedEvent(
        action_id=action.action_id,
        error=RustProtocolError(
            # A prompt over the model's window fails the same way on every retry.
            code="context_overflow"
            if failure.context_overflow
            else "model_stream_failed",
            message=failure.message,
            retryable=False,
            details=_failure_details(action, provider, route, exc),
        ),
    )


def _refusal_event(
    action: RustLLMCallAction,
    refusal: ProviderRefusal,
    *,
    provider: str,
    route: LocalModelRoute,
) -> RustCompletionFailedEvent:
    """Fail the call rather than accept an answer the model declined to give.

    Accepting it would make the declined turn part of the conversation that
    every later request repeats.
    """
    details = _failure_details(action, provider, route, refusal)
    if refusal.category is not None:
        details["category"] = refusal.category
    if refusal.explanation is not None:
        details["explanation"] = refusal.explanation
    return RustCompletionFailedEvent(
        action_id=action.action_id,
        error=RustProtocolError(
            code="model_refused", message=str(refusal), retryable=False, details=details
        ),
    )


def _failure_details(
    action: RustLLMCallAction, provider: str, route: LocalModelRoute, exc: BaseException
) -> JsonObject:
    details: JsonObject = {
        "provider": provider,
        "model": route.model,
        "purpose": action.purpose,
        "exceptionType": type(exc).__name__,
    }
    response = read_provider_failure(exc).response
    if response is None:
        return details
    details["httpStatus"] = response.status
    # Only the failed response identifies this request; a previous response's
    # correlation ID belongs to a different completion or retry attempt.
    if correlation_id := response.headers.get(CORRELATION_ID_HEADER):
        details["correlationId"] = correlation_id
    return details


__all__ = [
    "INVALID_API_KEY_MESSAGE",
    "CompactionReasoningLimitExceeded",
    "credential_rejection",
    "execute_completion",
    "invalid_api_key_message",
]
