from __future__ import annotations

import atexit
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from opentelemetry import baggage, context, trace
from opentelemetry.semconv._incubating.attributes import gen_ai_attributes
from opentelemetry.trace import StatusCode

from vibe import __version__

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig

from vibe.core.logger import logger

VIBE_TRACER_NAME = "mistral_vibe"
VIBE_AGENT_NAME = "mistral-vibe"


def setup_tracing(config: VibeConfig) -> None:
    if not config.enable_otel:
        return

    exporter_cfg = config.otel_exporter_config
    if exporter_cfg is None:
        return

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({
        "service.name": VIBE_AGENT_NAME,
        "service.version": __version__,
    })
    exporter = OTLPSpanExporter(**exporter_cfg.model_dump())
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    atexit.register(provider.shutdown)


def _get_tracer() -> trace.Tracer:
    return trace.get_tracer(VIBE_TRACER_NAME, __version__)


@asynccontextmanager
async def _safe_span(
    name: str, attributes: dict[str, Any]
) -> AsyncGenerator[trace.Span]:
    # Tracing errors are logged, never raised.
    try:
        tracer = _get_tracer()
        cm = tracer.start_as_current_span(name, attributes=attributes)
        span = cm.__enter__()
    except Exception:
        logger.warning("Failed to create span", exc_info=True)
        yield trace.INVALID_SPAN
        return

    exc_info: BaseException | None = None
    try:
        yield span
    except BaseException as exc:
        exc_info = exc
        raise
    finally:
        try:
            if isinstance(exc_info, Exception):
                span.set_status(StatusCode.ERROR, str(exc_info))
                span.record_exception(exc_info)
            elif exc_info is None:
                span.set_status(StatusCode.OK)
        except Exception:
            logger.warning("Failed to record span status", exc_info=True)
        finally:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                logger.warning("Failed to end span", exc_info=True)


@asynccontextmanager
async def agent_span(
    *, model: str | None = None, session_id: str | None = None
) -> AsyncGenerator[trace.Span]:
    attributes: dict[str, Any] = {
        gen_ai_attributes.GEN_AI_OPERATION_NAME: gen_ai_attributes.GenAiOperationNameValues.INVOKE_AGENT.value,
        gen_ai_attributes.GEN_AI_PROVIDER_NAME: gen_ai_attributes.GenAiProviderNameValues.MISTRAL_AI.value,
        gen_ai_attributes.GEN_AI_AGENT_NAME: VIBE_AGENT_NAME,
    }
    if model:
        attributes[gen_ai_attributes.GEN_AI_REQUEST_MODEL] = model
    if session_id:
        attributes[gen_ai_attributes.GEN_AI_CONVERSATION_ID] = session_id

    # Propagate conversation ID as OTEL baggage so descendant spans — including
    # those created by the Mistral SDK — can read and attach it.
    token = None
    if session_id:
        ctx = baggage.set_baggage(gen_ai_attributes.GEN_AI_CONVERSATION_ID, session_id)
        token = context.attach(ctx)
    try:
        async with _safe_span(f"invoke_agent {VIBE_AGENT_NAME}", attributes) as span:
            yield span
    finally:
        if token is not None:
            context.detach(token)


@asynccontextmanager
async def tool_span(
    *, tool_name: str, call_id: str, arguments: str
) -> AsyncGenerator[trace.Span]:
    attributes: dict[str, Any] = {
        gen_ai_attributes.GEN_AI_OPERATION_NAME: gen_ai_attributes.GenAiOperationNameValues.EXECUTE_TOOL.value,
        gen_ai_attributes.GEN_AI_TOOL_NAME: tool_name,
        gen_ai_attributes.GEN_AI_TOOL_CALL_ID: call_id,
        gen_ai_attributes.GEN_AI_TOOL_CALL_ARGUMENTS: arguments,
        gen_ai_attributes.GEN_AI_TOOL_TYPE: "function",
    }
    if conv_id := baggage.get_baggage(gen_ai_attributes.GEN_AI_CONVERSATION_ID):
        attributes[gen_ai_attributes.GEN_AI_CONVERSATION_ID] = conv_id

    async with _safe_span(f"execute_tool {tool_name}", attributes) as span:
        yield span


def set_tool_result(span: trace.Span, result: str) -> None:
    try:
        span.set_attribute(gen_ai_attributes.GEN_AI_TOOL_CALL_RESULT, result)
    except Exception:
        pass
