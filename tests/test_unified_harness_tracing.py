from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

from mistralai.extra.observability import telemetry as mistral_telemetry
from opentelemetry import trace as otel_trace
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.sdk.trace import TracerProvider
import pytest

from mistralai_vibe_local_harness.protocol import (
    RustCompletionResult,
    RustLLMCallAction,
    RustModelInputUpdate,
    RustModelMessageReplace,
    RustModelToolCatalogKeep,
    RustProvidedToolCall,
    RustProvidedToolCallAction,
    RustRuntimeBuiltinToolCall,
    RustRuntimeBuiltinToolCallAction,
    RustTextContentBlock,
)
from mistralai_vibe_local_harness.vibe import (
    LocalModelRoute,
    LocalRuntimeAdapterConfig,
    OtelTracingHooks,
    _completion,
    _local_actions,
)
from mistralai_vibe_local_harness.vibe._credentials import ProviderCredentialSnapshot
from mistralai_vibe_local_harness.vibe.adapters import mistral as mistral_adapter
from tests.conftest import build_test_vibe_config
from vibe.app_server import _runtime as runtime_module
from vibe.core import tracing as core_tracing
from vibe.core.config import OtelRedactionMode


def _recording_span(
    events: list[tuple[str, str, object]], name: str
) -> Callable[..., AbstractAsyncContextManager[Any]]:
    @asynccontextmanager
    async def span(**attributes: object):
        events.append((name, "enter", attributes))
        try:
            yield None
        except BaseException:
            events.append((name, "error", None))
            raise
        else:
            events.append((name, "exit", None))

    return span


def _tracing_hooks(events: list[tuple[str, str, object]]) -> OtelTracingHooks:
    return OtelTracingHooks(
        agent_span=_recording_span(events, "agent"),
        model_call_span=_recording_span(events, "model"),
        tool_span=_recording_span(events, "tool"),
    )


def _otlp_handler(received_span_names: list[str]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/v1/traces":
                self.send_error(404)
                return
            content_length = int(self.headers.get("Content-Length", "0"))
            request = ExportTraceServiceRequest()
            request.ParseFromString(self.rfile.read(content_length))
            received_span_names.extend(
                span.name
                for resource_spans in request.resource_spans
                for scope_spans in resource_spans.scope_spans
                for span in scope_spans.spans
            )
            body = ExportTraceServiceResponse().SerializeToString()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    return Handler


def _completion_action(*, purpose: str = "agent") -> RustLLMCallAction:
    fields: dict[str, object] = {
        "action_id": "action-1",
        "turn_id": "turn-1",
        "purpose": purpose,
        "iteration": 0,
        "max_iterations": 3,
        "model_input": RustModelInputUpdate(
            messages=RustModelMessageReplace(revision=0, messages=[]),
            tool_catalog=RustModelToolCatalogKeep(revision=0),
        ),
    }
    if purpose == "compaction":
        fields.update(
            compaction_id="compaction-1",
            attempt=1,
            trigger="automatic",
            request="in_context",
        )
    return RustLLMCallAction.model_validate(fields)


def _completion_result() -> RustCompletionResult:
    return RustCompletionResult(
        parts=[RustTextContentBlock(text="hello")], finish_reason="stop"
    )


def _stub_completion_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_completion, "_emit_request_sent", lambda *args: None)
    monkeypatch.setattr(
        _completion, "_report_malformed_tool_calls", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(_completion, "_validate_image_support", lambda *args: None)


def test_unified_process_configures_tracing_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = object.__new__(runtime_module.HarnessProcess)
    process._tracing_configured = False
    setup = Mock()
    monkeypatch.setattr(runtime_module, "_otel_enabled", lambda _config: True)
    monkeypatch.setattr(runtime_module, "setup_tracing", setup)
    config = build_test_vibe_config()

    process._configure_tracing(config)
    process._configure_tracing(config)

    setup.assert_called_once()
    assert process._tracing_configured is True


def test_tracing_configuration_is_preserved_when_forking_runtime_config() -> None:
    hooks = _tracing_hooks([])
    config = LocalRuntimeAdapterConfig(enable_otel=True, tracing=hooks)

    child = config.forked_with(workspace=config.workspace, ports=config.fork_ports())

    assert child.enable_otel is True
    assert child.tracing is hooks


@pytest.mark.asyncio
async def test_unified_tracing_exports_agent_model_and_tool_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_span_names: list[str] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _otlp_handler(received_span_names))
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    providers: list[TracerProvider] = []
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setattr(otel_trace, "set_tracer_provider", providers.append)
    monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: providers[-1])
    monkeypatch.setattr(
        otel_trace,
        "get_tracer",
        lambda name, version=None: providers[-1].get_tracer(name, version),
    )
    monkeypatch.setattr(core_tracing.atexit, "register", Mock())

    process = object.__new__(runtime_module.HarnessProcess)
    process._tracing_configured = False
    endpoint = f"http://127.0.0.1:{server.server_address[1]}"
    config = build_test_vibe_config().model_copy(
        update={
            "enable_telemetry": True,
            "enable_otel": True,
            "otel_endpoint": endpoint,
            "otel_redaction": OtelRedactionMode.NONE,
        }
    )
    try:
        process._configure_tracing(config)
        assert process._tracing_configured is True
        hooks = OtelTracingHooks(
            agent_span=core_tracing.agent_span,
            model_call_span=core_tracing.model_call_span,
            tool_span=core_tracing.tool_span,
        )
        _stub_completion_setup(monkeypatch)

        async def complete(*args: Any, **kwargs: Any) -> RustCompletionResult:
            return _completion_result()

        monkeypatch.setattr(_completion, "execute_mistral_completion", complete)
        completion_config = LocalRuntimeAdapterConfig(tracing=hooks)
        result = await _completion.execute_completion(
            _completion_action(), [], [], completion_config
        )
        assert result.type == "completion_succeeded"

        state = object.__new__(_local_actions._LocalActionState)
        state._config = completion_config
        monkeypatch.setattr(
            _local_actions._LocalActionState, "_execute", AsyncMock(return_value="done")
        )
        action = RustRuntimeBuiltinToolCallAction(
            action_id="action-2",
            turn_id="turn-1",
            call_id="call-2",
            call=RustRuntimeBuiltinToolCall(
                name="file_system.bash", arguments={"command": "pwd"}
            ),
        )
        await state.execute(action)

        assert providers[-1].force_flush()
        assert set(received_span_names) == {
            "invoke_agent mistral-vibe",
            f"chat {LocalModelRoute().model}",
            "execute_tool file_system.bash",
        }
    finally:
        if providers:
            providers[-1].shutdown()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


@pytest.mark.asyncio
async def test_unified_completion_records_agent_and_model_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str, object]] = []
    _stub_completion_setup(monkeypatch)

    async def complete(*args: Any, **kwargs: Any) -> RustCompletionResult:
        return _completion_result()

    monkeypatch.setattr(_completion, "execute_mistral_completion", complete)
    config = LocalRuntimeAdapterConfig(tracing=_tracing_hooks(events))

    result = await _completion.execute_completion(_completion_action(), [], [], config)

    assert result.type == "completion_succeeded"
    assert result.result == _completion_result()
    assert [(name, state) for name, state, _ in events] == [
        ("agent", "enter"),
        ("model", "enter"),
        ("model", "exit"),
        ("agent", "exit"),
    ]
    model_attributes = events[1][2]
    assert isinstance(model_attributes, dict)
    assert model_attributes["metadata"]["call_type"] == "agent"


@pytest.mark.asyncio
async def test_unified_completion_closes_model_span_on_failure_without_agent_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str, object]] = []
    _stub_completion_setup(monkeypatch)

    async def fail(*args: Any, **kwargs: Any) -> RustCompletionResult:
        raise RuntimeError("provider failed")

    monkeypatch.setattr(_completion, "execute_mistral_completion", fail)
    config = LocalRuntimeAdapterConfig(tracing=_tracing_hooks(events))

    result = await _completion.execute_completion(
        _completion_action(purpose="compaction"), [], [], config
    )

    assert result.type == "completion_failed"
    assert [(name, state) for name, state, _ in events] == [
        ("model", "enter"),
        ("model", "error"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("provided", [False, True])
async def test_unified_tool_action_records_arguments_and_closes_span(
    monkeypatch: pytest.MonkeyPatch, provided: bool
) -> None:
    events: list[tuple[str, str, object]] = []
    state = object.__new__(_local_actions._LocalActionState)
    state._config = LocalRuntimeAdapterConfig(tracing=_tracing_hooks(events))
    execute = AsyncMock(return_value="done")
    monkeypatch.setattr(_local_actions._LocalActionState, "_execute", execute)
    if provided:
        action = RustProvidedToolCallAction(
            action_id="action-1",
            turn_id="turn-1",
            call_id="call-1",
            call=RustProvidedToolCall(
                group_name="mcp", tool_name="search", arguments={"query": "repo"}
            ),
        )
        expected_tool_name = "mcp.search"
        expected_arguments = '{"query": "repo"}'
    else:
        action = RustRuntimeBuiltinToolCallAction(
            action_id="action-1",
            turn_id="turn-1",
            call_id="call-1",
            call=RustRuntimeBuiltinToolCall(
                name="file_system.bash", arguments={"command": "pwd"}
            ),
        )
        expected_tool_name = "file_system.bash"
        expected_arguments = '{"command": "pwd"}'

    result = await state.execute(action)

    assert result == "done"
    assert [(name, state) for name, state, _ in events] == [
        ("tool", "enter"),
        ("tool", "exit"),
    ]
    attributes = events[0][2]
    assert isinstance(attributes, dict)
    assert attributes == {
        "tool_name": expected_tool_name,
        "call_id": "call-1",
        "arguments": expected_arguments,
    }
    execute.assert_awaited_once_with(action)


@pytest.mark.asyncio
async def test_unified_tool_span_closes_when_execution_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str, object]] = []
    state = object.__new__(_local_actions._LocalActionState)
    state._config = LocalRuntimeAdapterConfig(tracing=_tracing_hooks(events))
    execute = AsyncMock(side_effect=RuntimeError("tool failed"))
    monkeypatch.setattr(_local_actions._LocalActionState, "_execute", execute)
    action = RustRuntimeBuiltinToolCallAction(
        action_id="action-1",
        turn_id="turn-1",
        call_id="call-1",
        call=RustRuntimeBuiltinToolCall(name="file_system.bash"),
    )

    with pytest.raises(RuntimeError, match="tool failed"):
        await state.execute(action)

    assert [(name, state) for name, state, _ in events] == [
        ("tool", "enter"),
        ("tool", "error"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_mistral_adapter_configures_sdk_telemetry_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="hello", tool_calls=None),
                finish_reason="stop",
            )
        ]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(complete_async=AsyncMock(return_value=response))
    )
    configure = Mock()
    monkeypatch.setattr(mistral_adapter, "Mistral", lambda **kwargs: client)
    monkeypatch.setattr(mistral_telemetry, "configure_telemetry", configure)
    config = LocalRuntimeAdapterConfig(enable_otel=enabled)
    credential = ProviderCredentialSnapshot(
        token="test-key", headers={"Authorization": "Bearer test-key"}, revision="r1"
    )

    await mistral_adapter.execute_mistral_completion(
        [], [], config, credential, LocalModelRoute(), stream=False
    )

    if enabled:
        configure.assert_called_once_with(client, provider="global")
    else:
        configure.assert_not_called()
