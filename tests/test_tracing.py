from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import StatusCode
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core import tracing
from vibe.core.config import OtelSpanExporterConfig
from vibe.core.tools.base import BaseToolConfig, ToolPermission
from vibe.core.tracing import agent_span, setup_tracing, tool_span
from vibe.core.types import BaseEvent, FunctionCall, ToolCall


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
        config = MagicMock(enable_otel=False)
        with patch("vibe.core.tracing.trace.set_tracer_provider") as mock_set:
            setup_tracing(config)
        mock_set.assert_not_called()

    def test_noop_when_exporter_config_is_none(self) -> None:
        config = MagicMock(enable_otel=True, otel_span_exporter_config=None)
        with patch("vibe.core.tracing.trace.set_tracer_provider") as mock_set:
            setup_tracing(config)
        mock_set.assert_not_called()

    def test_configures_provider_from_exporter_config(self) -> None:
        config = MagicMock(
            enable_otel=True,
            otel_span_exporter_config=OtelSpanExporterConfig(
                endpoint="https://customer.mistral.ai/telemetry/v1/traces",
                headers={"Authorization": "Bearer sk-test"},
            ),
        )

        with (
            patch(
                "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter"
            ) as mock_exporter,
            patch("vibe.core.tracing.trace.set_tracer_provider") as mock_set,
        ):
            setup_tracing(config)

        mock_exporter.assert_called_once_with(
            endpoint="https://customer.mistral.ai/telemetry/v1/traces",
            headers={"Authorization": "Bearer sk-test"},
        )
        mock_set.assert_called_once()
        assert isinstance(mock_set.call_args[0][0], TracerProvider)

    def test_custom_endpoint_has_no_auth_headers(self) -> None:
        config = MagicMock(
            enable_otel=True,
            otel_span_exporter_config=OtelSpanExporterConfig(
                endpoint="https://my-collector:4318/v1/traces"
            ),
        )

        with (
            patch(
                "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter"
            ) as mock_exporter,
            patch("vibe.core.tracing.trace.set_tracer_provider") as mock_set,
        ):
            setup_tracing(config)

        mock_exporter.assert_called_once_with(
            endpoint="https://my-collector:4318/v1/traces", headers=None
        )
        mock_set.assert_called_once()
        assert isinstance(mock_set.call_args[0][0], TracerProvider)


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
            system_prompt_id="tests",
            include_project_context=False,
            include_prompt_detail=False,
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
            "message: Retrieved 0 todos\ntodos: []\ntotal_count: 0"
        )
        # Conversation ID propagated via baggage from agent_span
        assert tool_attrs["gen_ai.conversation.id"] == agent_loop.session_id
