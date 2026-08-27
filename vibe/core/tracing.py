from __future__ import annotations

import atexit
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

# opentelemetry is imported inside the functions below: spans only run during
# agent turns and the exporter stack pulls in protobuf, so neither belongs on
# the CLI startup path.
from vibe import __version__
from vibe.core.config import (
    DEFAULT_MISTRAL_API_ENV_KEY,
    DEFAULT_MISTRAL_SERVER_URL,
    OtelRedactionMode,
    OtelSpanExporterConfig,
)
from vibe.utils.api_keys import resolve_api_key
from vibe.utils.http import get_server_url_from_api_base

if TYPE_CHECKING:
    from opentelemetry import trace

    from vibe.core.config import ProviderConfig, VibeConfigSchema
    from vibe.core.llm.exceptions import BackendError

from vibe.observability.logging import logger

VIBE_TRACER_NAME = "mistral_vibe"
VIBE_AGENT_NAME = "mistral-vibe"
MISTRAL_OTEL_PATH = "/telemetry"
VIBE_PROVIDER_API_STYLE = "vibe.provider.api_style"
VIBE_REQUEST_CALL_TYPE = "vibe.request.call_type"
VIBE_REQUEST_MESSAGE_ID = "vibe.request.message_id"
VIBE_REQUEST_STREAMING = "vibe.request.streaming"
_PUBLIC_REGIONAL_MISTRAL_API_HOSTS = frozenset({
    "api.eu.mistral.ai",
    "api.us.mistral.ai",
})


def _get_default_otel_server_url(mistral_provider: ProviderConfig | None) -> str:
    if mistral_provider is None:
        return DEFAULT_MISTRAL_SERVER_URL
    server_url = get_server_url_from_api_base(mistral_provider.api_base)
    if server_url is None:
        return DEFAULT_MISTRAL_SERVER_URL
    if urlparse(server_url).hostname in _PUBLIC_REGIONAL_MISTRAL_API_HOSTS:
        return DEFAULT_MISTRAL_SERVER_URL
    return server_url


def build_otel_span_exporter_config(
    otel_endpoint: str | None, mistral_provider: ProviderConfig | None
) -> OtelSpanExporterConfig | None:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        DEFAULT_TRACES_EXPORT_PATH,
    )

    # When otel_endpoint is set explicitly, authentication is the user's responsibility
    # (via OTEL_EXPORTER_OTLP_* env vars), so headers are left empty.
    # Otherwise endpoint and API key are derived from the given Mistral provider,
    # except public regional Mistral API hosts that do not serve telemetry.
    traces_export_path = DEFAULT_TRACES_EXPORT_PATH.lstrip("/")
    if otel_endpoint:
        return OtelSpanExporterConfig(
            endpoint=urljoin(f"{otel_endpoint.rstrip('/')}/", traces_export_path)
        )

    if mistral_provider is not None:
        api_key_env = mistral_provider.api_key_env_var or DEFAULT_MISTRAL_API_ENV_KEY
    else:
        api_key_env = DEFAULT_MISTRAL_API_ENV_KEY

    endpoint = urljoin(
        f"{urljoin(_get_default_otel_server_url(mistral_provider), MISTRAL_OTEL_PATH).rstrip('/')}/",
        traces_export_path,
    )

    if not (api_key := resolve_api_key(api_key_env)):
        logger.warning("OTEL tracing enabled but %s is not set; skipping.", api_key_env)
        return None

    return OtelSpanExporterConfig(
        endpoint=endpoint, headers={"Authorization": f"Bearer {api_key}"}
    )


def setup_tracing(config: VibeConfigSchema) -> None:
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
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

    resource = Resource.create({
        "service.name": VIBE_AGENT_NAME,
        "service.version": __version__,
    })
    exporter: SpanExporter = OTLPSpanExporter(**exporter_cfg.model_dump())
    if config.otel_redaction is not OtelRedactionMode.NONE:
        from mistralai.extra.observability import (
            AttributeRedactionPolicy,
            RedactingSpanExporter,
            default_redaction_policy,
        )

        policy = (
            AttributeRedactionPolicy()
            if config.otel_redaction is OtelRedactionMode.STRICT
            else default_redaction_policy()
        )
        exporter = RedactingSpanExporter(exporter, policy)
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    atexit.register(provider.shutdown)


def _get_tracer() -> trace.Tracer:
    from opentelemetry import trace

    return trace.get_tracer(VIBE_TRACER_NAME, __version__)


def _backend_error_from(exc: BaseException) -> BackendError | None:
    from vibe.core.llm.exceptions import BackendError

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, BackendError):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _exception_status(
    exc: Exception, *, record_unhandled_exception: bool
) -> tuple[str, bool]:
    if backend_error := _backend_error_from(exc):
        status = (
            str(backend_error.status) if backend_error.status is not None else "N/A"
        )
        return (
            f"BackendError(provider={backend_error.provider}, status={status})",
            False,
        )

    if not record_unhandled_exception:
        return f"{type(exc).__name__} raised during model call.", False

    return str(exc), True


@asynccontextmanager
async def _safe_span(
    name: str, attributes: dict[str, Any], *, record_unhandled_exception: bool = True
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
                description, should_record_exception = _exception_status(
                    exc_info, record_unhandled_exception=record_unhandled_exception
                )
                span.set_status(StatusCode.ERROR, description)
                if should_record_exception:
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


def _provider_attribute_value(provider_name: str | None) -> str:
    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes

    provider_name = provider_name or ""
    normalized_name = provider_name.strip().lower().replace("-", "_")
    known_values = {value.value for value in gen_ai_attributes.GenAiProviderNameValues}

    aliases = {
        "mistral": gen_ai_attributes.GenAiProviderNameValues.MISTRAL_AI.value,
        "mistral_ai": gen_ai_attributes.GenAiProviderNameValues.MISTRAL_AI.value,
        "google_vertex": gen_ai_attributes.GenAiProviderNameValues.GCP_VERTEX_AI.value,
        "vertex": gen_ai_attributes.GenAiProviderNameValues.GCP_VERTEX_AI.value,
        "google": gen_ai_attributes.GenAiProviderNameValues.GCP_GEN_AI.value,
        "azure_openai": gen_ai_attributes.GenAiProviderNameValues.AZURE_AI_OPENAI.value,
        "bedrock": gen_ai_attributes.GenAiProviderNameValues.AWS_BEDROCK.value,
        "xai": gen_ai_attributes.GenAiProviderNameValues.X_AI.value,
    }

    if normalized_name in aliases:
        return aliases[normalized_name]
    if normalized_name in known_values:
        return normalized_name
    return normalized_name or "unknown"


@asynccontextmanager
async def model_call_span(
    *,
    provider_name: str,
    provider_api_style: str,
    model: str,
    streaming: bool,
    temperature: float | None = None,
    max_tokens: int | None = None,
    metadata: dict[str, str] | None = None,
    http_method: str = "POST",
    http_url: str | None = None,
) -> AsyncGenerator[trace.Span]:
    from opentelemetry import baggage
    from opentelemetry.semconv._incubating.attributes import (
        gen_ai_attributes,
        http_attributes,
    )

    attributes: dict[str, Any] = {
        gen_ai_attributes.GEN_AI_OPERATION_NAME: gen_ai_attributes.GenAiOperationNameValues.CHAT.value,
        gen_ai_attributes.GEN_AI_PROVIDER_NAME: _provider_attribute_value(
            provider_name
        ),
        gen_ai_attributes.GEN_AI_REQUEST_MODEL: model,
        VIBE_PROVIDER_API_STYLE: provider_api_style,
        VIBE_REQUEST_STREAMING: streaming,
    }
    if temperature is not None:
        attributes[gen_ai_attributes.GEN_AI_REQUEST_TEMPERATURE] = temperature
    if max_tokens is not None:
        attributes[gen_ai_attributes.GEN_AI_REQUEST_MAX_TOKENS] = max_tokens
    if http_url:
        attributes[http_attributes.HTTP_REQUEST_METHOD] = http_method
        attributes[http_attributes.HTTP_URL] = http_url

    metadata = metadata or {}
    conversation_id = baggage.get_baggage(gen_ai_attributes.GEN_AI_CONVERSATION_ID)
    if conversation_id is None:
        conversation_id = metadata.get("session_id")
    if conversation_id:
        attributes[gen_ai_attributes.GEN_AI_CONVERSATION_ID] = str(conversation_id)

    if call_type := metadata.get("call_type"):
        attributes[VIBE_REQUEST_CALL_TYPE] = call_type
    if message_id := metadata.get("message_id"):
        attributes[VIBE_REQUEST_MESSAGE_ID] = message_id

    async with _safe_span(
        f"chat {model}", attributes, record_unhandled_exception=False
    ) as span:
        yield span


def set_model_call_http_status(span: trace.Span | None, status_code: int) -> None:
    if span is None:
        return

    from opentelemetry.semconv._incubating.attributes import http_attributes

    try:
        span.set_attribute(http_attributes.HTTP_RESPONSE_STATUS_CODE, status_code)
    except Exception:
        pass


def set_model_call_usage(
    span: trace.Span | None, *, prompt_tokens: int, completion_tokens: int
) -> None:
    if span is None:
        return

    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes

    try:
        span.set_attribute(gen_ai_attributes.GEN_AI_USAGE_INPUT_TOKENS, prompt_tokens)
        span.set_attribute(
            gen_ai_attributes.GEN_AI_USAGE_OUTPUT_TOKENS, completion_tokens
        )
    except Exception:
        pass


def _string_attribute(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _response_finish_reasons(response_data: dict[str, Any]) -> list[str]:
    choices = response_data.get("choices")
    finish_reasons: list[str] = []
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            if reason := _string_attribute(choice.get("finish_reason")):
                finish_reasons.append(reason)

    for source in (
        response_data,
        response_data.get("delta"),
        response_data.get("response"),
    ):
        if not isinstance(source, dict):
            continue
        if reason := _string_attribute(
            source.get("finish_reason") or source.get("stop_reason")
        ):
            finish_reasons.append(reason)

    return finish_reasons


def _response_metadata_sources(response_data: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [response_data]
    for key in ("message", "response"):
        if isinstance(source := response_data.get(key), dict):
            sources.append(source)
    return sources


def set_model_call_response_metadata(
    span: trace.Span | None, response_data: dict[str, Any]
) -> None:
    if span is None:
        return

    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes

    attributes: dict[str, str | list[str]] = {}
    sources = _response_metadata_sources(response_data)
    if response_model := next(
        (
            model
            for source in sources
            if (model := _string_attribute(source.get("model")))
        ),
        None,
    ):
        attributes[gen_ai_attributes.GEN_AI_RESPONSE_MODEL] = response_model
    if response_id := next(
        (
            response_id
            for source in sources
            if (response_id := _string_attribute(source.get("id")))
        ),
        None,
    ):
        attributes[gen_ai_attributes.GEN_AI_RESPONSE_ID] = response_id
    if finish_reasons := _response_finish_reasons(response_data):
        attributes[gen_ai_attributes.GEN_AI_RESPONSE_FINISH_REASONS] = finish_reasons

    try:
        for key, value in attributes.items():
            span.set_attribute(key, value)
    except Exception:
        pass


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
