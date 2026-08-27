from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import httpx
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import StatusCode
import pytest
import respx

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.constants import CHAT_COMPLETIONS_PATH
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core import tracing
from vibe.core.config import (
    ModelConfig,
    OtelRedactionMode,
    OtelSpanExporterConfig,
    ProviderConfig,
)
from vibe.core.llm.backend.generic import GenericBackend
from vibe.core.llm.exceptions import BackendError
from vibe.core.tools.base import BaseToolConfig, ToolPermission
from vibe.core.tracing import agent_span, setup_tracing, tool_span
from vibe.core.types import BaseEvent, FunctionCall, LLMMessage, Role, ToolCall


class _CollectingExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list = []

    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _otel_provider(monkeypatch: pytest.MonkeyPatch):
    # Patch get_tracer_provider instead of set_tracer_provider to sidestep the
    # OTEL singleton guard that rejects a second set_tracer_provider call.
    exporter = _CollectingExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    yield exporter


class TestSetupTracing:
    def test_noop_when_disabled(self) -> None:
        config = MagicMock(enable_telemetry=True, enable_otel=False)
        with patch("opentelemetry.trace.set_tracer_provider") as mock_set:
            setup_tracing(config)
        mock_set.assert_not_called()

    def test_noop_when_telemetry_disabled(self) -> None:
        config = MagicMock(enable_telemetry=False, enable_otel=True)
        with patch("opentelemetry.trace.set_tracer_provider") as mock_set:
            setup_tracing(config)
        mock_set.assert_not_called()

    def test_noop_when_exporter_config_is_none(self) -> None:
        config = MagicMock(enable_telemetry=True, enable_otel=True)
        with (
            patch(
                "vibe.core.tracing.build_otel_span_exporter_config", return_value=None
            ),
            patch("opentelemetry.trace.set_tracer_provider") as mock_set,
        ):
            setup_tracing(config)
        mock_set.assert_not_called()

    def test_configures_provider_from_exporter_config(self) -> None:
        config = MagicMock(enable_telemetry=True, enable_otel=True)

        with (
            patch(
                "vibe.core.tracing.build_otel_span_exporter_config",
                return_value=OtelSpanExporterConfig(
                    endpoint="https://customer.mistral.ai/telemetry/v1/traces",
                    headers={"Authorization": "Bearer sk-test"},
                ),
            ),
            patch(
                "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter"
            ) as mock_exporter,
            patch("opentelemetry.trace.set_tracer_provider") as mock_set,
        ):
            setup_tracing(config)

        mock_exporter.assert_called_once_with(
            endpoint="https://customer.mistral.ai/telemetry/v1/traces",
            headers={"Authorization": "Bearer sk-test"},
        )
        mock_set.assert_called_once()
        assert isinstance(mock_set.call_args[0][0], TracerProvider)

    def test_custom_endpoint_has_no_auth_headers(self) -> None:
        config = MagicMock(enable_telemetry=True, enable_otel=True)

        with (
            patch(
                "vibe.core.tracing.build_otel_span_exporter_config",
                return_value=OtelSpanExporterConfig(
                    endpoint="https://my-collector:4318/v1/traces"
                ),
            ),
            patch(
                "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter"
            ) as mock_exporter,
            patch("opentelemetry.trace.set_tracer_provider") as mock_set,
        ):
            setup_tracing(config)

        mock_exporter.assert_called_once_with(
            endpoint="https://my-collector:4318/v1/traces", headers=None
        )
        mock_set.assert_called_once()
        assert isinstance(mock_set.call_args[0][0], TracerProvider)


class TestSetupTracingRedaction:
    @staticmethod
    def _export_span_attributes(
        mode: OtelRedactionMode, attributes: dict[str, str]
    ) -> dict[str, object]:
        collector = _CollectingExporter()
        config = MagicMock(enable_telemetry=True, enable_otel=True, otel_redaction=mode)
        with (
            patch(
                "vibe.core.tracing.build_otel_span_exporter_config",
                return_value=OtelSpanExporterConfig(endpoint="https://x/v1/traces"),
            ),
            patch(
                "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
                return_value=collector,
            ),
            patch("opentelemetry.trace.set_tracer_provider") as mock_set,
        ):
            setup_tracing(config)

        provider = mock_set.call_args[0][0]
        with provider.get_tracer("test").start_as_current_span(
            "chat", attributes=attributes
        ):
            pass
        provider.force_flush()

        assert len(collector.spans) == 1
        return dict(collector.spans[0].attributes)

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            pytest.param(
                OtelRedactionMode.DEFAULT,
                {"gen_ai.input.messages": "hi there, my email is [REDACTED]"},
                id="default-redacts-matched-values",
            ),
            pytest.param(
                OtelRedactionMode.STRICT,
                {"gen_ai.input.messages": "[REDACTED]"},
                id="strict-redacts-sensitive-keys",
            ),
            pytest.param(
                OtelRedactionMode.NONE,
                {"gen_ai.input.messages": "hi there, my email is admin@example.com"},
                id="none-leaves-attributes-intact",
            ),
        ],
    )
    def test_policy_applied_to_exported_span(
        self, mode: OtelRedactionMode, expected: dict[str, str]
    ) -> None:
        assert (
            self._export_span_attributes(
                mode,
                attributes={
                    "gen_ai.input.messages": "hi there, my email is admin@example.com"
                },
            )
            == expected
        )


class TestAgentSpan:
    @pytest.mark.asyncio
    async def test_span_name_status_and_attributes(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        async with agent_span(model="devstral", session_id="s1"):
            pass

        assert len(_otel_provider.spans) == 1
        span = _otel_provider.spans[0]
        assert span.name == "invoke_agent mistral-vibe"
        assert span.status.status_code == StatusCode.OK
        attrs = dict(span.attributes)
        assert attrs["gen_ai.operation.name"] == "invoke_agent"
        assert attrs["gen_ai.provider.name"] == "mistral_ai"
        assert attrs["gen_ai.agent.name"] == "mistral-vibe"
        assert attrs["gen_ai.request.model"] == "devstral"
        assert attrs["gen_ai.conversation.id"] == "s1"

    @pytest.mark.asyncio
    async def test_omits_optional_attributes(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        async with agent_span():
            pass

        attrs = dict(_otel_provider.spans[0].attributes)
        assert "gen_ai.request.model" not in attrs
        assert "gen_ai.conversation.id" not in attrs

    @pytest.mark.asyncio
    async def test_records_error_on_exception(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        with pytest.raises(ValueError, match="boom"):
            async with agent_span():
                raise ValueError("boom")

        span = _otel_provider.spans[0]
        assert span.status.status_code == StatusCode.ERROR
        assert "boom" in span.status.description


class TestToolSpan:
    @pytest.mark.asyncio
    async def test_span_name_status_and_attributes(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        async with tool_span(tool_name="bash", call_id="c1", arguments='{"cmd": "ls"}'):
            pass

        assert len(_otel_provider.spans) == 1
        span = _otel_provider.spans[0]
        assert span.name == "execute_tool bash"
        assert span.status.status_code == StatusCode.OK
        attrs = dict(span.attributes)
        assert attrs["gen_ai.operation.name"] == "execute_tool"
        assert attrs["gen_ai.tool.name"] == "bash"
        assert attrs["gen_ai.tool.call.id"] == "c1"
        assert attrs["gen_ai.tool.call.arguments"] == '{"cmd": "ls"}'
        assert attrs["gen_ai.tool.type"] == "function"

    @pytest.mark.asyncio
    async def test_records_error_and_exception_event(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        with pytest.raises(RuntimeError):
            async with tool_span(tool_name="bash", call_id="c1", arguments="{}"):
                raise RuntimeError("fail")

        span = _otel_provider.spans[0]
        assert span.status.status_code == StatusCode.ERROR
        exc_events = [e for e in span.events if e.name == "exception"]
        assert len(exc_events) == 1


class TestSpanHierarchy:
    @pytest.mark.asyncio
    async def test_chat_and_tool_are_siblings_under_agent(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        async with agent_span(model="devstral"):
            tracer = trace.get_tracer("mistralai_sdk_tracer")
            # Simulate a chat span created by the Mistral SDK.
            with tracer.start_as_current_span("chat devstral"):
                pass

            async with tool_span(tool_name="grep", call_id="c1", arguments="{}"):
                pass

            with tracer.start_as_current_span("chat devstral"):
                pass

        agent = next(s for s in _otel_provider.spans if "invoke_agent" in s.name)
        children = [
            s
            for s in _otel_provider.spans
            if s.parent and s.parent.span_id == agent.context.span_id
        ]
        assert len(children) == 3
        assert [s.name for s in children] == [
            "chat devstral",
            "execute_tool grep",
            "chat devstral",
        ]


class TestGenericModelCallSpan:
    BASE_URL = "https://api.fireworks.ai"
    MODEL_NAME = "accounts/fireworks/models/mistral-test"

    @staticmethod
    def _provider(base_url: str = BASE_URL) -> ProviderConfig:
        return ProviderConfig(
            name="fireworks",
            api_base=f"{base_url}/v1",
            api_key_env_var="",
            api_style="openai",
        )

    @staticmethod
    def _model() -> ModelConfig:
        return ModelConfig(
            name=TestGenericModelCallSpan.MODEL_NAME,
            provider="fireworks",
            alias="mistral-test",
        )

    @staticmethod
    def _messages() -> list[LLMMessage]:
        return [LLMMessage(role=Role.user, content="Just say hi")]

    @classmethod
    def _backend(
        cls, *, retry_max_elapsed_time: float = 300.0
    ) -> tuple[GenericBackend, ModelConfig]:
        return (
            GenericBackend(
                provider=cls._provider(),
                enable_otel=True,
                retry_max_elapsed_time=retry_max_elapsed_time,
            ),
            cls._model(),
        )

    @staticmethod
    def _chat_response(
        model_name: str, *, prompt_tokens: int = 12, completion_tokens: int = 5
    ) -> dict:
        return {
            "id": "cmpl_123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "hi"},
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        }

    @staticmethod
    def _chat_span(spans):
        return next(s for s in spans if s.name.startswith("chat "))

    @staticmethod
    def _assert_spans_do_not_contain(spans, *values: str) -> None:
        for span in spans:
            for value in values:
                assert value not in (span.status.description or "")
            for event in span.events:
                event_attrs = str(dict(event.attributes or {}))
                for value in values:
                    assert value not in event_attrs

    @classmethod
    def _sse_data(cls, payload: object) -> bytes:
        if isinstance(payload, str):
            body = payload
        else:
            body = json.dumps(payload, separators=(",", ":"))
        return f"data: {body}".encode()

    @classmethod
    def _sse_chat_chunk(
        cls,
        *,
        content: str | None = None,
        finish_reason: str | None,
        usage: dict[str, int] | None = None,
    ) -> bytes:
        delta = {}
        if content is not None:
            delta = {"role": "assistant", "content": content}

        return cls._sse_data({
            "id": "cmpl_123",
            "object": "chat.completion.chunk",
            "model": cls.MODEL_NAME,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            "usage": usage,
        })

    @pytest.mark.asyncio
    async def test_generic_complete_creates_model_call_span(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        backend, model = self._backend()

        with respx.mock(base_url=self.BASE_URL) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(
                    status_code=200,
                    json=self._chat_response(
                        model.name, prompt_tokens=19, completion_tokens=7
                    ),
                )
            )

            async with agent_span(model=model.name, session_id="sess-1"):
                await backend.complete(
                    model=model,
                    messages=self._messages(),
                    temperature=0.4,
                    tools=None,
                    max_tokens=123,
                    tool_choice=None,
                    extra_headers=None,
                    metadata={"call_type": "main_call", "message_id": "msg-1"},
                )

        agent = next(s for s in _otel_provider.spans if "invoke_agent" in s.name)
        chat = self._chat_span(_otel_provider.spans)
        assert chat.parent is not None
        assert chat.parent.span_id == agent.context.span_id

        attrs = dict(chat.attributes)
        assert attrs["gen_ai.operation.name"] == "chat"
        assert attrs["gen_ai.provider.name"] == "fireworks"
        assert attrs["gen_ai.request.model"] == model.name
        assert attrs["gen_ai.request.temperature"] == 0.4
        assert attrs["gen_ai.request.max_tokens"] == 123
        assert attrs["gen_ai.response.id"] == "cmpl_123"
        assert attrs["gen_ai.response.model"] == model.name
        assert attrs["gen_ai.response.finish_reasons"] == ("stop",)
        assert attrs["gen_ai.conversation.id"] == "sess-1"
        assert attrs["gen_ai.usage.input_tokens"] == 19
        assert attrs["gen_ai.usage.output_tokens"] == 7
        assert attrs["http.request.method"] == "POST"
        assert attrs["http.url"] == f"{self.BASE_URL}{CHAT_COMPLETIONS_PATH}"
        assert attrs["http.response.status_code"] == 200
        assert attrs["vibe.provider.api_style"] == "openai"
        assert attrs["vibe.request.call_type"] == "main_call"
        assert attrs["vibe.request.message_id"] == "msg-1"
        assert attrs["vibe.request.streaming"] is False

    @pytest.mark.asyncio
    async def test_model_call_span_uses_provider_name_over_api_style(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        async with tracing.model_call_span(
            provider_name="custom-provider",
            provider_api_style="vertex-anthropic",
            model=self.MODEL_NAME,
            streaming=False,
        ):
            pass

        chat = self._chat_span(_otel_provider.spans)
        attrs = dict(chat.attributes)
        assert attrs["gen_ai.provider.name"] == "custom_provider"
        assert attrs["vibe.provider.api_style"] == "vertex-anthropic"

    @pytest.mark.asyncio
    async def test_generic_streaming_creates_one_model_call_span(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        backend, model = self._backend()
        chunks = [
            self._sse_chat_chunk(content="hi", finish_reason=None),
            self._sse_chat_chunk(
                finish_reason="stop",
                usage={"prompt_tokens": 23, "completion_tokens": 11},
            ),
            self._sse_data("[DONE]"),
        ]

        with respx.mock(base_url=self.BASE_URL) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=b"\n\n".join(chunks)),
                    headers={"Content-Type": "text/event-stream"},
                )
            )

            async with agent_span(model=model.name, session_id="sess-2"):
                results = [
                    chunk
                    async for chunk in backend.complete_streaming(
                        model=model,
                        messages=self._messages(),
                        temperature=0.2,
                        tools=None,
                        max_tokens=None,
                        tool_choice=None,
                        extra_headers=None,
                        metadata={"call_type": "secondary_call"},
                    )
                ]

        assert [chunk.message.content for chunk in results] == ["hi", ""]
        chat_spans = [s for s in _otel_provider.spans if s.name.startswith("chat ")]
        assert len(chat_spans) == 1

        attrs = dict(chat_spans[0].attributes)
        assert attrs["gen_ai.operation.name"] == "chat"
        assert attrs["gen_ai.response.id"] == "cmpl_123"
        assert attrs["gen_ai.response.model"] == self.MODEL_NAME
        assert attrs["gen_ai.response.finish_reasons"] == ("stop",)
        assert attrs["gen_ai.conversation.id"] == "sess-2"
        assert attrs["gen_ai.usage.input_tokens"] == 23
        assert attrs["gen_ai.usage.output_tokens"] == 11
        assert attrs["http.response.status_code"] == 200
        assert attrs["vibe.request.call_type"] == "secondary_call"
        assert attrs["vibe.request.streaming"] is True

    @pytest.mark.asyncio
    async def test_generic_empty_stream_records_http_status(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        backend, model = self._backend()

        with respx.mock(base_url=self.BASE_URL) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=self._sse_data("[DONE]")),
                    headers={"Content-Type": "text/event-stream"},
                )
            )

            results = [
                chunk
                async for chunk in backend.complete_streaming(
                    model=model,
                    messages=self._messages(),
                    temperature=0.2,
                    tools=None,
                    max_tokens=None,
                    tool_choice=None,
                    extra_headers=None,
                )
            ]

        assert results == []
        chat = self._chat_span(_otel_provider.spans)
        attrs = dict(chat.attributes)
        assert attrs["http.response.status_code"] == 200
        assert "gen_ai.usage.input_tokens" not in attrs
        assert "gen_ai.usage.output_tokens" not in attrs

    @pytest.mark.asyncio
    async def test_generic_stream_without_usage_omits_usage_attributes(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        backend, model = self._backend()
        chunks = [
            self._sse_chat_chunk(content="hi", finish_reason=None),
            self._sse_chat_chunk(finish_reason="stop"),
            self._sse_data("[DONE]"),
        ]

        with respx.mock(base_url=self.BASE_URL) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=b"\n\n".join(chunks)),
                    headers={"Content-Type": "text/event-stream"},
                )
            )

            results = [
                chunk
                async for chunk in backend.complete_streaming(
                    model=model, messages=self._messages()
                )
            ]

        assert [chunk.message.content for chunk in results] == ["hi", ""]
        attrs = dict(self._chat_span(_otel_provider.spans).attributes)
        assert "gen_ai.usage.input_tokens" not in attrs
        assert "gen_ai.usage.output_tokens" not in attrs

    @pytest.mark.asyncio
    async def test_generic_complete_without_usage_omits_usage_attributes(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        backend, model = self._backend()
        response = self._chat_response(model.name)
        response.pop("usage")

        with respx.mock(base_url=self.BASE_URL) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(status_code=200, json=response)
            )

            await backend.complete(model=model, messages=self._messages())

        attrs = dict(self._chat_span(_otel_provider.spans).attributes)
        assert "gen_ai.usage.input_tokens" not in attrs
        assert "gen_ai.usage.output_tokens" not in attrs

    @pytest.mark.asyncio
    async def test_generic_streaming_http_error_records_http_status(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        backend, model = self._backend(retry_max_elapsed_time=0.0)

        with respx.mock(base_url=self.BASE_URL) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(status_code=429, text="rate limited")
            )

            with pytest.raises(BackendError):
                async for _ in backend.complete_streaming(
                    model=model,
                    messages=self._messages(),
                    temperature=0.2,
                    tools=None,
                    max_tokens=None,
                    tool_choice=None,
                    extra_headers=None,
                ):
                    pass

        chat = self._chat_span(_otel_provider.spans)
        attrs = dict(chat.attributes)
        assert chat.status.status_code == StatusCode.ERROR
        assert attrs["http.response.status_code"] == 429

    @pytest.mark.asyncio
    async def test_generic_streaming_request_error_omits_retried_http_status(
        self, _otel_provider: _CollectingExporter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = [0.0]
        monkeypatch.setattr("vibe.core.utils.retry.time.monotonic", lambda: now[0])

        async def advance_clock(seconds: float) -> None:
            now[0] += seconds

        monkeypatch.setattr("vibe.core.utils.retry.asyncio.sleep", advance_clock)
        backend, model = self._backend(retry_max_elapsed_time=1.0)

        with respx.mock(base_url=self.BASE_URL) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                side_effect=[
                    httpx.Response(status_code=503),
                    httpx.ConnectError("connection refused"),
                    httpx.ConnectError("connection refused"),
                ]
            )

            with pytest.raises(BackendError):
                async for _ in backend.complete_streaming(
                    model=model, messages=self._messages()
                ):
                    pass

        chat = self._chat_span(_otel_provider.spans)
        attrs = dict(chat.attributes)
        assert chat.status.status_code == StatusCode.ERROR
        assert "http.response.status_code" not in attrs

    @pytest.mark.asyncio
    async def test_generic_malformed_response_records_http_status(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        backend, model = self._backend()

        with respx.mock(base_url=self.BASE_URL) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(status_code=200, content=b"not json")
            )

            with pytest.raises(json.JSONDecodeError):
                await backend.complete(model=model, messages=self._messages())

        chat = self._chat_span(_otel_provider.spans)
        assert chat.status.status_code == StatusCode.ERROR
        assert dict(chat.attributes)["http.response.status_code"] == 200

    @pytest.mark.asyncio
    async def test_generic_malformed_stream_error_omits_raw_line_from_span(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        backend, model = self._backend()
        sensitive_line = "SECRET_PROMPT_DO_NOT_EXPORT"

        with respx.mock(base_url=self.BASE_URL) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=sensitive_line.encode()),
                    headers={"Content-Type": "text/event-stream"},
                )
            )

            with pytest.raises(ValueError) as exc_info:
                async for _ in backend.complete_streaming(
                    model=model,
                    messages=self._messages(),
                    temperature=0.2,
                    tools=None,
                    max_tokens=None,
                    tool_choice=None,
                    extra_headers=None,
                ):
                    pass

        assert sensitive_line not in str(exc_info.value)
        chat = self._chat_span(_otel_provider.spans)
        assert chat.status.status_code == StatusCode.ERROR
        self._assert_spans_do_not_contain(_otel_provider.spans, sensitive_line)

    @pytest.mark.asyncio
    async def test_generic_malformed_stream_json_omits_payload_from_span(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        backend, model = self._backend()
        sensitive_payload = '{"content":"SECRET_PROMPT_DO_NOT_EXPORT"'

        with respx.mock(base_url=self.BASE_URL) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=self._sse_data(sensitive_payload)),
                    headers={"Content-Type": "text/event-stream"},
                )
            )

            with pytest.raises(ValueError) as exc_info:
                async for _ in backend.complete_streaming(
                    model=model,
                    messages=self._messages(),
                    temperature=0.2,
                    tools=None,
                    max_tokens=None,
                    tool_choice=None,
                    extra_headers=None,
                ):
                    pass

        assert exc_info.typename == "ValueError"
        assert str(exc_info.value) == "Stream chunk contains malformed JSON."
        chat = self._chat_span(_otel_provider.spans)
        assert chat.status.status_code == StatusCode.ERROR
        self._assert_spans_do_not_contain(_otel_provider.spans, sensitive_payload)

    @pytest.mark.asyncio
    async def test_generic_parse_stream_error_omits_payload_from_span(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        provider = ProviderConfig(
            name="anthropic",
            api_base=self.BASE_URL,
            api_key_env_var="",
            api_style="anthropic",
        )
        model = ModelConfig(name="claude-test", provider="anthropic", alias="anthropic")
        backend = GenericBackend(provider=provider, enable_otel=True)
        sensitive_payload = "SECRET_PROMPT_DO_NOT_EXPORT"

        with respx.mock(base_url=self.BASE_URL) as mock_api:
            mock_api.post("/v1/messages").mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(
                        stream=self._sse_data({
                            "type": "error",
                            "error": {
                                "type": "overloaded_error",
                                "message": sensitive_payload,
                            },
                        })
                    ),
                    headers={"Content-Type": "text/event-stream"},
                )
            )

            with pytest.raises(RuntimeError) as exc_info:
                async for _ in backend.complete_streaming(
                    model=model,
                    messages=self._messages(),
                    temperature=0.2,
                    tools=None,
                    max_tokens=None,
                    tool_choice=None,
                    extra_headers=None,
                ):
                    pass

        assert sensitive_payload in str(exc_info.value)
        chat = self._chat_span(_otel_provider.spans)
        assert chat.status.status_code == StatusCode.ERROR
        self._assert_spans_do_not_contain(_otel_provider.spans, sensitive_payload)

    @pytest.mark.asyncio
    async def test_generic_http_error_marks_model_call_span(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        backend, model = self._backend()
        sensitive_echo = "SECRET_PROMPT_DO_NOT_EXPORT"

        with respx.mock(base_url=self.BASE_URL) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(
                    status_code=400,
                    json={
                        "error": {"message": f"Rejected prompt: {sensitive_echo}"},
                        "echo": sensitive_echo,
                    },
                )
            )

            with pytest.raises(BackendError) as exc_info:
                async with agent_span(model=model.name, session_id="sess-3"):
                    await backend.complete(
                        model=model,
                        messages=self._messages(),
                        temperature=0.2,
                        tools=None,
                        max_tokens=None,
                        tool_choice=None,
                        extra_headers=None,
                    )

        assert sensitive_echo in str(exc_info.value)
        chat = self._chat_span(_otel_provider.spans)
        attrs = dict(chat.attributes)
        assert chat.status.status_code == StatusCode.ERROR
        assert chat.status.description == "BackendError(provider=fireworks, status=400)"
        assert attrs["http.response.status_code"] == 400
        self._assert_spans_do_not_contain(
            _otel_provider.spans, sensitive_echo, "body_excerpt"
        )


class TestModelCallResponseMetadata:
    @pytest.mark.asyncio
    async def test_supports_anthropic_stream_metadata(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        async with tracing.model_call_span(
            provider_name="anthropic",
            provider_api_style="anthropic",
            model="claude-test",
            streaming=True,
        ) as span:
            tracing.set_model_call_response_metadata(
                span, {"message": {"id": "msg_123", "model": "claude-test"}}
            )
            tracing.set_model_call_response_metadata(
                span, {"delta": {"stop_reason": "end_turn"}}
            )

        attrs = dict(_otel_provider.spans[0].attributes)
        assert attrs["gen_ai.response.id"] == "msg_123"
        assert attrs["gen_ai.response.model"] == "claude-test"
        assert attrs["gen_ai.response.finish_reasons"] == ("end_turn",)

    @pytest.mark.asyncio
    async def test_supports_openai_responses_metadata(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        async with tracing.model_call_span(
            provider_name="openai",
            provider_api_style="openai-responses",
            model="gpt-test",
            streaming=True,
        ) as span:
            tracing.set_model_call_response_metadata(
                span,
                {
                    "response": {
                        "id": "resp_123",
                        "model": "gpt-test",
                        "finish_reason": "stop",
                    }
                },
            )

        attrs = dict(_otel_provider.spans[0].attributes)
        assert attrs["gen_ai.response.id"] == "resp_123"
        assert attrs["gen_ai.response.model"] == "gpt-test"
        assert attrs["gen_ai.response.finish_reasons"] == ("stop",)


class TestBaggagePropagation:
    @pytest.mark.asyncio
    async def test_tool_span_inherits_conversation_id(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        async with agent_span(model="devstral", session_id="sess-42"):
            async with tool_span(tool_name="bash", call_id="c1", arguments="{}"):
                pass

        tool = next(s for s in _otel_provider.spans if "execute_tool" in s.name)
        assert dict(tool.attributes)["gen_ai.conversation.id"] == "sess-42"

    @pytest.mark.asyncio
    async def test_tool_span_omits_conversation_id_when_no_session(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        async with agent_span(model="devstral"):
            async with tool_span(tool_name="bash", call_id="c1", arguments="{}"):
                pass

        tool = next(s for s in _otel_provider.spans if "execute_tool" in s.name)
        assert "gen_ai.conversation.id" not in dict(tool.attributes)

    @pytest.mark.asyncio
    async def test_baggage_does_not_leak_after_agent_span(self) -> None:
        from opentelemetry import baggage as baggage_api

        async with agent_span(model="devstral", session_id="sess-1"):
            pass

        assert baggage_api.get_baggage("gen_ai.conversation.id") is None


class TestErrorIsolation:
    @pytest.mark.asyncio
    async def test_yields_invalid_span_on_creation_failure(
        self, _otel_provider: _CollectingExporter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _broken_tracer() -> trace.Tracer:
            raise RuntimeError("tracer broken")

        monkeypatch.setattr(tracing, "_get_tracer", _broken_tracer)

        async with agent_span():
            pass

        assert len(_otel_provider.spans) == 0

    @pytest.mark.asyncio
    async def test_caller_exception_propagates_when_set_status_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _broken_set_status(self, *args, **kwargs):
            raise RuntimeError("set_status broken")

        monkeypatch.setattr(
            "opentelemetry.sdk.trace.Span.set_status", _broken_set_status
        )

        with pytest.raises(ValueError, match="original"):
            async with agent_span():
                raise ValueError("original")

    @pytest.mark.asyncio
    async def test_cancellation_ends_span_without_error_status(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        with pytest.raises(asyncio.CancelledError):
            async with agent_span():
                raise asyncio.CancelledError

        span = _otel_provider.spans[0]
        assert span.status.status_code != StatusCode.ERROR

    @pytest.mark.asyncio
    async def test_success_path_swallows_span_end_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _broken_end(self, *args, **kwargs):
            raise RuntimeError("end broken")

        monkeypatch.setattr("opentelemetry.sdk.trace.Span.end", _broken_end)

        async with agent_span():
            pass


class TestIntegration:
    @staticmethod
    async def _collect_events(agent_loop, prompt: str) -> list[BaseEvent]:
        return [ev async for ev in agent_loop.act(prompt)]

    @pytest.mark.asyncio
    async def test_agent_turn_with_tool_call_produces_spans(
        self, _otel_provider: _CollectingExporter
    ) -> None:
        tool_call = ToolCall(
            id="call_1",
            index=0,
            function=FunctionCall(name="todo", arguments='{"action": "read"}'),
        )
        backend = FakeBackend([
            [mock_llm_chunk(content="Let me check.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="Done.")],
        ])
        config = build_test_vibe_config(
            enabled_tools=["todo"],
            tools={"todo": BaseToolConfig(permission=ToolPermission.ALWAYS)},
        )
        agent_loop = build_test_agent_loop(config=config, backend=backend)

        await self._collect_events(agent_loop, "What are my todos?")

        spans = _otel_provider.spans
        agent_spans = [s for s in spans if "invoke_agent" in s.name]
        tool_spans = [s for s in spans if "execute_tool" in s.name]

        assert len(agent_spans) == 1
        assert len(tool_spans) == 1

        agent = agent_spans[0]
        tool = tool_spans[0]

        # Parent-child relationship
        assert tool.parent is not None
        assert tool.parent.span_id == agent.context.span_id

        # -- Agent span: name, status, and every attribute set by agent_span() --
        assert agent.name == "invoke_agent mistral-vibe"
        assert agent.status.status_code == StatusCode.OK
        agent_attrs = dict(agent.attributes)
        assert agent_attrs["gen_ai.operation.name"] == "invoke_agent"
        assert agent_attrs["gen_ai.provider.name"] == "mistral_ai"
        assert agent_attrs["gen_ai.agent.name"] == "mistral-vibe"
        assert agent_attrs["gen_ai.request.model"] == "mistral-vibe-cli-latest"
        assert agent_attrs["gen_ai.conversation.id"] == agent_loop.session_id

        # -- Tool span: name, status, and every attribute set by tool_span() + set_tool_result() --
        assert tool.name == "execute_tool todo"
        assert tool.status.status_code == StatusCode.OK
        tool_attrs = dict(tool.attributes)
        assert tool_attrs["gen_ai.operation.name"] == "execute_tool"
        assert tool_attrs["gen_ai.tool.name"] == "todo"
        assert tool_attrs["gen_ai.tool.call.id"] == "call_1"
        assert tool_attrs["gen_ai.tool.type"] == "function"
        assert (
            tool_attrs["gen_ai.tool.call.arguments"] == '{"action":"read","todos":null}'
        )
        assert tool_attrs["gen_ai.tool.call.result"] == (
            "verb: Retrieved\ntodos: []\ntotal_count: 0\nmessage: Retrieved 0 todos"
        )
        # Conversation ID propagated via baggage from agent_span
        assert tool_attrs["gen_ai.conversation.id"] == agent_loop.session_id
