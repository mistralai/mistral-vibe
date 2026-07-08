from __future__ import annotations

import atexit
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

# opentelemetry is imported inside the functions below: spans only run during
# agent turns and the exporter stack pulls in protobuf, so neither belongs on
# the CLI startup path.
from vibe import __version__
from vibe.core.config import (
    DEFAULT_MISTRAL_API_ENV_KEY,
    DEFAULT_MISTRAL_SERVER_URL,
    OtelSpanExporterConfig,
    resolve_api_key,
)
from vibe.core.utils import get_server_url_from_api_base

if TYPE_CHECKING:
    from opentelemetry import trace

    from vibe.core.config import AnyVibeConfig, ProviderConfig

from vibe.core.logger import logger

VIBE_TRACER_NAME = "mistral_vibe"
VIBE_AGENT_NAME = "mistral-vibe"
MISTRAL_OTEL_PATH = "/telemetry"


def build_otel_span_exporter_config(
    otel_endpoint: str | None, mistral_provider: ProviderConfig | None
) -> OtelSpanExporterConfig | None:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        DEFAULT_TRACES_EXPORT_PATH,
    )

    # When otel_endpoint is set explicitly, authentication is the user's responsibility
    # (via OTEL_EXPORTER_OTLP_* env vars), so headers are left empty.
    # Otherwise endpoint and API key are derived from the given Mistral provider.
    traces_export_path = DEFAULT_TRACES_EXPORT_PATH.lstrip("/")
    if otel_endpoint:
        return OtelSpanExporterConfig(
            endpoint=urljoin(f"{otel_endpoint.rstrip('/')}/", traces_export_path)
        )

    if mistral_provider is not None:
        server_url = get_server_url_from_api_base(mistral_provider.api_base)
        api_key_env = mistral_provider.api_key_env_var or DEFAULT_MISTRAL_API_ENV_KEY
    else:
        server_url = None
        api_key_env = DEFAULT_MISTRAL_API_ENV_KEY

    endpoint = urljoin(
        f"{urljoin(server_url or DEFAULT_MISTRAL_SERVER_URL, MISTRAL_OTEL_PATH).rstrip('/')}/",
        traces_export_path,
    )

    if not (api_key := resolve_api_key(api_key_env)):
        logger.warning("OTEL tracing enabled but %s is not set; skipping.", api_key_env)
        return None

    return OtelSpanExporterConfig(
        endpoint=endpoint, headers={"Authorization": f"Bearer {api_key}"}
    )


def setup_tracing(config: AnyVibeConfig) -> None:
    if not config.enable_telemetry or not config.enable_otel:
        return

    exporter_cfg = build_otel_span_exporter_config(
        config.otel_endpoint, config.get_mistral_provider()
    )
    if exporter_cfg is None:
        return

    from opentelemetry import trace
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
    from opentelemetry import trace

    return trace.get_tracer(VIBE_TRACER_NAME, __version__)


@asynccontextmanager
async def _safe_span(
    name: str, attributes: dict[str, Any]
) -> AsyncGenerator[trace.Span]:
    from opentelemetry import trace
    from opentelemetry.trace import StatusCode

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
    from opentelemetry import baggage, context
    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes

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
    from opentelemetry import baggage
    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes

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


@asynccontextmanager
async def hook_span(
    *,
    hook_name: str,
    hook_type: str,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
) -> AsyncGenerator[trace.Span]:
    from opentelemetry import baggage
    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes

    attributes: dict[str, Any] = {
        "vibe.hook.name": hook_name,
        "vibe.hook.type": hook_type,
    }
    if tool_name is not None:
        attributes[gen_ai_attributes.GEN_AI_TOOL_NAME] = tool_name
    if tool_call_id is not None:
        attributes[gen_ai_attributes.GEN_AI_TOOL_CALL_ID] = tool_call_id
    if conv_id := baggage.get_baggage(gen_ai_attributes.GEN_AI_CONVERSATION_ID):
        attributes[gen_ai_attributes.GEN_AI_CONVERSATION_ID] = conv_id

    async with _safe_span(f"hook {hook_type} {hook_name}", attributes) as span:
        yield span


def set_tool_result(span: trace.Span, result: str) -> None:
    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes

    try:
        span.set_attribute(gen_ai_attributes.GEN_AI_TOOL_CALL_RESULT, result)
    except Exception:
        pass
