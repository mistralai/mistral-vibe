"""Tracing utilities and decorators for Mistral Vibe observability."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
import inspect
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, TypedDict

from opentelemetry import trace, context
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer, INVALID_SPAN

from vibe.core.observability.metrics import (
    AGENT_EXECUTION_COUNT,
    AGENT_EXECUTION_DURATION,
    TOOL_EXECUTION_COUNT,
    TOOL_EXECUTION_DURATION,
    get_metrics_manager,
)
from vibe.core.observability.semconv import (
    ATTR_ERROR,
    ATTR_ERROR_MESSAGE,
    ATTR_GEN_AI_OPERATION_NAME,
    ATTR_GEN_AI_REQUEST_MAX_TOKENS,
    ATTR_GEN_AI_REQUEST_MODEL,
    ATTR_GEN_AI_REQUEST_TEMPERATURE,
    ATTR_GEN_AI_RESPONSE_MODEL,
    ATTR_GEN_AI_SYSTEM,
    ATTR_GEN_AI_USAGE_DURATION_MS,
    ATTR_LLM_ENDPOINT,
    ATTR_LLM_REQUEST_HAS_TOOLS,
    ATTR_LLM_REQUEST_TOOL_COUNT,
    ATTR_LLM_STREAMING,
    ATTR_TOOL_COMMAND,
    ATTR_TOOL_DURATION_MS,
    ATTR_TOOL_NAME,
    ATTR_TOOL_TYPE,
    OP_CHAT,
    get_agent_execution_attributes,
    get_llm_request_attributes,
    get_tool_execution_attributes,
)


class SpanMetadata(TypedDict, total=False):
    name: str
    input: Any
    output: Any
    error: Any
    attributes: dict[str, Any]


class SpanPayload(TypedDict):
    metadata: SpanMetadata
    end_span: Callable[[], Any]


# Re-export for backward compatibility
AGENT_EXECUTION_ATTRIBUTES = get_agent_execution_attributes()
TOOL_EXECUTION_ATTRIBUTES = get_tool_execution_attributes()
LLM_REQUEST_ATTRIBUTES = get_llm_request_attributes()


_TRACING_ENABLED = False
_SESSION_BASED_TRACE = False


def set_tracing_enabled(value: bool) -> None:
    """Set global tracing state (used by the SDK)."""
    global _TRACING_ENABLED
    _TRACING_ENABLED = value


def set_session_based_trace(value: bool) -> None:
    """Set session-based trace mode"""
    global _SESSION_BASED_TRACE
    _SESSION_BASED_TRACE = value


def _env_tracing_enabled() -> bool:
    env_value = os.environ.get("MISTRAL_VIBE_TELEMETRY_ENABLED")
    if env_value is None:
        return False
    return env_value.lower() in {"1", "true", "yes"}


def _is_tracing_enabled() -> bool:
    return _TRACING_ENABLED or _env_tracing_enabled()


def _is_session_based_trace() -> bool:
    """Check if session-based trace mode is enabled."""
    if _SESSION_BASED_TRACE:
        return True
    env_value = os.environ.get("MISTRAL_VIBE_TELEMETRY_SESSION_BASED_TRACE")
    if env_value is None:
        return False
    return env_value.lower() in {"1", "true", "yes"}


def get_tracer_provider() -> Tracer:
    """Get tracer for Mistral Vibe"""
    return trace.get_tracer("mistral-vibe")


def safe_json_serialize(obj: Any) -> str:
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return json.dumps({"error": "unable_to_serialize"})


def _finalize_span(span: Span | None, metadata: SpanMetadata) -> None:
    if span is None:
        return
    try:
        if "input" in metadata:
            span.set_attribute("input-json", safe_json_serialize(metadata["input"]))
        if "output" in metadata:
            span.set_attribute("output-json", safe_json_serialize(metadata["output"]))
        for key, value in metadata.get("attributes", {}).items():
            span.set_attribute(key, value)
        if "error" in metadata:
            error = metadata["error"]
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute(ATTR_ERROR, True)
            if isinstance(error, Exception):
                span.record_exception(error)
                span.set_attribute(ATTR_ERROR_MESSAGE, str(error))
            else:
                span.set_attribute(ATTR_ERROR_MESSAGE, safe_json_serialize(error))
        else:
            span.set_status(Status(StatusCode.OK))
    except Exception as exc:  # pragma: no cover - defensive
        span.set_status(Status(StatusCode.ERROR))
        span.set_attribute(ATTR_ERROR, True)
        span.set_attribute(ATTR_ERROR_MESSAGE, f"telemetry finalization failed: {exc}")


def _agent_attribute_defaults(agent: Any, call_kwargs: dict[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    agent_name = (
        getattr(getattr(agent, "__class__", None), "__name__", "UnknownAgent")
        if agent
        else "UnknownAgent"
    )
    attrs[ATTR_GEN_AI_REQUEST_MODEL] = agent_name
    attrs[ATTR_GEN_AI_RESPONSE_MODEL] = agent_name

    config = getattr(agent, "config", None)
    if config is not None:
        try:
            active_model = config.get_active_model()
            model_name = (
                getattr(active_model, "model", None)
                or getattr(active_model, "name", None)
                or getattr(active_model, "id", None)
            )
            if model_name:
                attrs[ATTR_GEN_AI_REQUEST_MODEL] = model_name
                attrs[ATTR_GEN_AI_RESPONSE_MODEL] = model_name
            temperature = getattr(active_model, "temperature", None)
            if temperature is not None:
                attrs[ATTR_GEN_AI_REQUEST_TEMPERATURE] = temperature
            max_tokens = getattr(active_model, "max_tokens", None)
            if max_tokens is not None:
                attrs[ATTR_GEN_AI_REQUEST_MAX_TOKENS] = max_tokens
        except Exception:
            pass

    if model := call_kwargs.get("model"):
        attrs[ATTR_GEN_AI_REQUEST_MODEL] = model
        attrs[ATTR_GEN_AI_RESPONSE_MODEL] = call_kwargs.get("response_model", model)
    if temperature := call_kwargs.get("temperature"):
        attrs[ATTR_GEN_AI_REQUEST_TEMPERATURE] = temperature
    if max_tokens := call_kwargs.get("max_tokens"):
        attrs[ATTR_GEN_AI_REQUEST_MAX_TOKENS] = max_tokens

    return attrs


def _tool_attribute_defaults(tool: Any) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    tool_name = getattr(tool, "name", None)
    if not tool_name and hasattr(tool, "get_name"):
        try:
            tool_name = tool.get_name()
        except Exception:
            tool_name = None
    attrs[ATTR_TOOL_NAME] = tool_name or "UnknownTool"

    tool_type = getattr(tool, "tool_type", None)
    if not tool_type:
        tool_type = getattr(getattr(tool, "__class__", None), "__name__", "Tool")
    attrs[ATTR_TOOL_TYPE] = tool_type

    return attrs


def _sanitize_metadata_payload(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _sanitize_metadata_payload(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_metadata_payload(v) for v in value]
    return str(value)


def build_llm_request_attributes(
    *,
    model_name: str,
    provider_name: str,
    temperature: float,
    max_tokens: int | None,
    streaming: bool,
    tool_count: int,
    endpoint: str,
) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        ATTR_GEN_AI_REQUEST_MODEL: model_name,
        ATTR_GEN_AI_REQUEST_TEMPERATURE: temperature,
        ATTR_GEN_AI_SYSTEM: provider_name,
        ATTR_GEN_AI_OPERATION_NAME: OP_CHAT,
        ATTR_LLM_STREAMING: streaming,
        ATTR_LLM_REQUEST_TOOL_COUNT: tool_count,
        ATTR_LLM_ENDPOINT: endpoint,
    }
    if max_tokens is not None:
        attributes[ATTR_GEN_AI_REQUEST_MAX_TOKENS] = max_tokens
    if tool_count:
        attributes[ATTR_LLM_REQUEST_HAS_TOOLS] = True
    return attributes


def run_in_trace_span(
    name: str,
    func: Callable[[SpanPayload], Any],
    span_kind: SpanKind = SpanKind.INTERNAL,
    initial_attributes: dict[str, Any] | None = None,
) -> Any:
    metadata: SpanMetadata = {
        "name": name,
        "attributes": dict(initial_attributes or {}),
    }

    if not _is_tracing_enabled():
        return func({"metadata": metadata, "end_span": lambda: None})

    tracer = get_tracer_provider()
    with tracer.start_as_current_span(name, kind=span_kind) as span:
        finalized = False

        def end_span() -> None:
            nonlocal finalized
            if finalized:
                return
            _finalize_span(span, metadata)
            finalized = True

        try:
            result = func({"metadata": metadata, "end_span": end_span})
        except Exception as exc:
            metadata["error"] = exc
            end_span()
            raise
        else:
            end_span()
            return result


async def run_in_trace_span_async(
    name: str,
    func: Callable[[SpanPayload], Awaitable[Any]],
    span_kind: SpanKind = SpanKind.INTERNAL,
    initial_attributes: dict[str, Any] | None = None,
    as_root_span: bool = False,
) -> Any:
    """Execute async function within a trace span.

    Args:
        name: Span name
        func: Async function to execute
        span_kind: OpenTelemetry span kind
        initial_attributes: Initial span attributes
        as_root_span: If True, creates a new root span (independent trace)
    """
    metadata: SpanMetadata = {
        "name": name,
        "attributes": dict(initial_attributes or {}),
    }

    async def _execute_without_tracing() -> Any:
        try:
            result = func({"metadata": metadata, "end_span": lambda: None})
        except Exception as exc:
            metadata["error"] = exc
            raise
        if inspect.isasyncgen(result):
            return result
        if inspect.isawaitable(result):
            return await result
        return result

    if not _is_tracing_enabled():
        return await _execute_without_tracing()

    tracer = get_tracer_provider()

    # If as_root_span is True, create span with empty context to start new trace
    if as_root_span:
        empty_context = trace.set_span_in_context(INVALID_SPAN)
        span_cm = tracer.start_as_current_span(name, kind=span_kind, context=empty_context)
    else:
        span_cm = tracer.start_as_current_span(name, kind=span_kind)

    span = span_cm.__enter__()
    finalized = False

    def _complete(exc: BaseException | None = None) -> None:
        nonlocal finalized
        if finalized:
            return
        if isinstance(exc, Exception):
            metadata["error"] = exc
        _finalize_span(span, metadata)
        finalized = True
        span_cm.__exit__(
            exc.__class__ if exc else None,
            exc,
            exc.__traceback__ if exc else None,
        )

    def _end_span_callback() -> None:
        _complete(None)

    try:
        result = func({"metadata": metadata, "end_span": _end_span_callback})
    except Exception as exc:
        _complete(exc)
        raise

    if inspect.isasyncgen(result):

        async def _wrapped_generator() -> Any:
            captured: BaseException | None = None
            try:
                async for item in result:
                    yield item
            except BaseException as error:
                captured = error
                raise
            finally:
                _complete(captured)

        return _wrapped_generator()

    async def _await_result(awaitable: Awaitable[Any]) -> Any:
        try:
            value = await awaitable
        except Exception as exc:
            _complete(exc)
            raise
        else:
            _complete(None)
            return value

    if inspect.isawaitable(result):
        return await _await_result(result)

    _complete(None)
    return result


@asynccontextmanager
async def async_run_in_trace_span(
    name: str,
    span_kind: SpanKind = SpanKind.INTERNAL,
    initial_attributes: dict[str, Any] | None = None,
    as_root_span: bool = False,
):
    """Create a trace span context manager.

    Args:
        name: Span name
        span_kind: OpenTelemetry span kind
        initial_attributes: Initial span attributes
        as_root_span: If True, creates a new root span (independent trace)
                      instead of a child span of the current context
    """
    metadata: SpanMetadata = {
        "name": name,
        "attributes": dict(initial_attributes or {}),
    }

    if not _is_tracing_enabled():
        async def noop() -> None:
            return None

        yield metadata, noop
        return

    tracer = get_tracer_provider()

    # If as_root_span is True, create span with empty context to start new trace
    if as_root_span:
        empty_context = trace.set_span_in_context(INVALID_SPAN)
        span_context = tracer.start_as_current_span(name, kind=span_kind, context=empty_context)
    else:
        span_context = tracer.start_as_current_span(name, kind=span_kind)

    with span_context as span:
        finalized = False

        async def end_span() -> None:
            nonlocal finalized
            if finalized:
                return
            _finalize_span(span, metadata)
            finalized = True

        try:
            yield metadata, end_span
        except Exception as exc:
            metadata["error"] = exc
            await end_span()
            raise
        finally:
            await end_span()


def trace_agent_execution(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        def _execute(span_payload: SpanPayload) -> Any:
            metadata = span_payload["metadata"]
            attributes = metadata.setdefault("attributes", {})
            agent = args[0] if args else None
            attributes.update(_agent_attribute_defaults(agent, kwargs))

            start_time = time.time()
            metrics = get_metrics_manager()
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                duration_ms = (time.time() - start_time) * 1000
                attributes[ATTR_GEN_AI_USAGE_DURATION_MS] = duration_ms
                metadata["error"] = exc
                metrics.get_counter(AGENT_EXECUTION_COUNT).add(1)
                metrics.get_histogram(AGENT_EXECUTION_DURATION).record(duration_ms)
                raise
            else:
                duration_ms = (time.time() - start_time) * 1000
                attributes[ATTR_GEN_AI_USAGE_DURATION_MS] = duration_ms
                metrics.get_counter(AGENT_EXECUTION_COUNT).add(1)
                metrics.get_histogram(AGENT_EXECUTION_DURATION).record(duration_ms)
                return result

        return run_in_trace_span(
            "AgentExecution",
            _execute,
            span_kind=SpanKind.SERVER,
            initial_attributes=AGENT_EXECUTION_ATTRIBUTES,
        )

    return wrapper


def trace_agent_execution_async(func: Callable) -> Callable:
    """Decorator for async agent execution tracing.

    By default (session_based_trace=False), each agent execution creates
    an independent trace. When session_based_trace=True, agent executions
    are nested as child spans under the parent trace.
    """
    if inspect.isasyncgenfunction(func):

        @wraps(func)
        async def generator_wrapper(*args: Any, **kwargs: Any):
            # Create independent trace unless session_based_trace is enabled
            as_root = not _is_session_based_trace()
            async with async_run_in_trace_span(
                "AgentExecution",
                span_kind=SpanKind.SERVER,
                initial_attributes=AGENT_EXECUTION_ATTRIBUTES,
                as_root_span=as_root,
            ) as (metadata, end_span):
                attributes = metadata.setdefault("attributes", {}).copy()
                metadata["attributes"] = attributes
                agent = args[0] if args else None
                attributes.update(_agent_attribute_defaults(agent, kwargs))
                
                # Record input (user prompt)
                # args[1] is typically the user message for agent.act(msg)
                if len(args) > 1:
                    metadata["input"] = {"prompt": args[1]}
                elif "msg" in kwargs:
                    metadata["input"] = {"prompt": kwargs["msg"]}

                start_time = time.time()
                metrics = get_metrics_manager()
                output_events: list[str] = []
                try:
                    async for value in func(*args, **kwargs):
                        # Collect output events for tracing
                        if hasattr(value, "content") and value.content:
                            output_events.append(str(value.content))
                        yield value
                except Exception as exc:
                    duration_ms = (time.time() - start_time) * 1000
                    attributes[ATTR_GEN_AI_USAGE_DURATION_MS] = duration_ms
                    metadata["error"] = exc
                    metrics.get_counter(AGENT_EXECUTION_COUNT).add(1)
                    metrics.get_histogram(AGENT_EXECUTION_DURATION).record(duration_ms)
                    raise
                else:
                    duration_ms = (time.time() - start_time) * 1000
                    attributes[ATTR_GEN_AI_USAGE_DURATION_MS] = duration_ms
                    metrics.get_counter(AGENT_EXECUTION_COUNT).add(1)
                    metrics.get_histogram(AGENT_EXECUTION_DURATION).record(duration_ms)
                    # Record output (agent response)
                    if output_events:
                        metadata["output"] = {"response": "".join(output_events)}
                finally:
                    await end_span()

        return generator_wrapper

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        async def _execute(span_payload: SpanPayload) -> Any:
            metadata = span_payload["metadata"]
            attributes = metadata.setdefault("attributes", {})
            agent = args[0] if args else None
            attributes.update(_agent_attribute_defaults(agent, kwargs))
            
            # Record input (user prompt)
            if len(args) > 1:
                metadata["input"] = {"prompt": args[1]}
            elif "msg" in kwargs:
                metadata["input"] = {"prompt": kwargs["msg"]}

            start_time = time.time()
            metrics = get_metrics_manager()
            try:
                result = await func(*args, **kwargs)
            except Exception as exc:
                duration_ms = (time.time() - start_time) * 1000
                attributes[ATTR_GEN_AI_USAGE_DURATION_MS] = duration_ms
                metadata["error"] = exc
                metrics.get_counter(AGENT_EXECUTION_COUNT).add(1)
                metrics.get_histogram(AGENT_EXECUTION_DURATION).record(duration_ms)
                raise
            else:
                duration_ms = (time.time() - start_time) * 1000
                attributes[ATTR_GEN_AI_USAGE_DURATION_MS] = duration_ms
                metrics.get_counter(AGENT_EXECUTION_COUNT).add(1)
                metrics.get_histogram(AGENT_EXECUTION_DURATION).record(duration_ms)
                # Record output
                if result is not None:
                    if hasattr(result, "content"):
                        metadata["output"] = {"response": str(result.content)}
                    else:
                        metadata["output"] = {"response": str(result)}
                return result

        return await run_in_trace_span_async(
            "AgentExecution",
            _execute,
            span_kind=SpanKind.SERVER,
            initial_attributes=AGENT_EXECUTION_ATTRIBUTES,
            as_root_span=not _is_session_based_trace(),
        )

    return wrapper


def trace_tool_execution(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        def _execute(span_payload: SpanPayload) -> Any:
            metadata = span_payload["metadata"]
            attributes = metadata.setdefault("attributes", {})
            tool = args[0] if args else None
            attributes.update(_tool_attribute_defaults(tool))
            if kwargs:
                metadata["input"] = _sanitize_metadata_payload(kwargs)
                if command := kwargs.get("command"):
                    attributes[ATTR_TOOL_COMMAND] = command

            start_time = time.time()
            metrics = get_metrics_manager()
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                duration_ms = (time.time() - start_time) * 1000
                attributes[ATTR_TOOL_DURATION_MS] = duration_ms
                metadata["error"] = exc
                metrics.get_counter(TOOL_EXECUTION_COUNT).add(1)
                metrics.get_histogram(TOOL_EXECUTION_DURATION).record(duration_ms)
                raise
            else:
                duration_ms = (time.time() - start_time) * 1000
                attributes[ATTR_TOOL_DURATION_MS] = duration_ms
                metrics.get_counter(TOOL_EXECUTION_COUNT).add(1)
                metrics.get_histogram(TOOL_EXECUTION_DURATION).record(duration_ms)
                # Record output
                if result is not None:
                    metadata["output"] = _sanitize_metadata_payload({"result": result})
                return result

        return run_in_trace_span(
            "ToolExecution",
            _execute,
            span_kind=SpanKind.INTERNAL,
            initial_attributes=TOOL_EXECUTION_ATTRIBUTES,
        )

    return wrapper


def trace_tool_execution_async(func: Callable) -> Callable:
    if inspect.isasyncgenfunction(func):

        @wraps(func)
        async def generator_wrapper(*args: Any, **kwargs: Any):
            async with async_run_in_trace_span(
                "ToolExecution",
                span_kind=SpanKind.INTERNAL,
                initial_attributes=TOOL_EXECUTION_ATTRIBUTES,
            ) as (metadata, end_span):
                attributes = metadata.setdefault("attributes", {}).copy()
                metadata["attributes"] = attributes
                tool = args[0] if args else None
                attributes.update(_tool_attribute_defaults(tool))
                if kwargs:
                    metadata["input"] = _sanitize_metadata_payload(kwargs)
                    if command := kwargs.get("command"):
                        attributes[ATTR_TOOL_COMMAND] = command

                start_time = time.time()
                metrics = get_metrics_manager()
                output_values: list[Any] = []
                try:
                    async for value in func(*args, **kwargs):
                        output_values.append(value)
                        yield value
                except Exception as exc:
                    duration_ms = (time.time() - start_time) * 1000
                    attributes[ATTR_TOOL_DURATION_MS] = duration_ms
                    metadata["error"] = exc
                    metrics.get_counter(TOOL_EXECUTION_COUNT).add(1)
                    metrics.get_histogram(TOOL_EXECUTION_DURATION).record(duration_ms)
                    raise
                else:
                    duration_ms = (time.time() - start_time) * 1000
                    attributes[ATTR_TOOL_DURATION_MS] = duration_ms
                    metrics.get_counter(TOOL_EXECUTION_COUNT).add(1)
                    metrics.get_histogram(TOOL_EXECUTION_DURATION).record(duration_ms)
                    # Record output
                    if output_values:
                        metadata["output"] = _sanitize_metadata_payload({"result": output_values})
                finally:
                    await end_span()

        return generator_wrapper

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        async def _execute(span_payload: SpanPayload) -> Any:
            metadata = span_payload["metadata"]
            attributes = metadata.setdefault("attributes", {})
            tool = args[0] if args else None
            attributes.update(_tool_attribute_defaults(tool))
            if kwargs:
                metadata["input"] = _sanitize_metadata_payload(kwargs)
                if command := kwargs.get("command"):
                    attributes[ATTR_TOOL_COMMAND] = command

            start_time = time.time()
            metrics = get_metrics_manager()
            try:
                result = await func(*args, **kwargs)
            except Exception as exc:
                duration_ms = (time.time() - start_time) * 1000
                attributes[ATTR_TOOL_DURATION_MS] = duration_ms
                metadata["error"] = exc
                metrics.get_counter(TOOL_EXECUTION_COUNT).add(1)
                metrics.get_histogram(TOOL_EXECUTION_DURATION).record(duration_ms)
                raise
            else:
                duration_ms = (time.time() - start_time) * 1000
                attributes[ATTR_TOOL_DURATION_MS] = duration_ms
                metrics.get_counter(TOOL_EXECUTION_COUNT).add(1)
                metrics.get_histogram(TOOL_EXECUTION_DURATION).record(duration_ms)
                # Record output
                if result is not None:
                    metadata["output"] = _sanitize_metadata_payload({"result": result})
                return result

        return await run_in_trace_span_async(
            "ToolExecution",
            _execute,
            span_kind=SpanKind.INTERNAL,
            initial_attributes=TOOL_EXECUTION_ATTRIBUTES,
        )

    return wrapper


def create_span(
    name: str,
    span_kind: SpanKind = SpanKind.INTERNAL,
    attributes: dict[str, Any] | None = None,
) -> Span:
    tracer = get_tracer_provider()
    span = tracer.start_span(name, kind=span_kind)
    if attributes:
        for key, value in attributes.items():
            span.set_attribute(key, value)
    return span


def get_current_span() -> Span | None:
    return trace.get_current_span()
