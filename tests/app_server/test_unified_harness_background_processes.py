from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Any, Literal, cast

import pytest

pytest.importorskip("mistralai_vibe_local_harness.session_protocol")
vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
pytest.importorskip("mistralai_vibe_local_harness.vibe._processes._output")

from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
    RustCompletionResult,
    RustCompletionResultPart,
    RustCompletionResultToolCallPart,
    RustCompletionSucceededEvent,
    RustLLMCallAction,
    RustTextContentBlock,
    RustTokenUsage,
)
from mistralai_vibe_local_harness.vibe._processes._output import (  # pyright: ignore[reportMissingImports]
    ProcessOutputStore,
)
from mistralai_vibe_local_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
    UnifiedSessionStore,
)

from vibe.app_server._runtime import HarnessProcess
from vibe.app_server._session_backend_port import SessionBackend, SessionBackendHost
from vibe.app_server._unified_harness_backend_adapter import (
    UnifiedSessionContext,
    UnifiedSessionSettings,
    adapt_harness_host,
)
from vibe.app_server.events import CallbackRequested, TurnCompleted
from vibe.app_server.models import (
    ApprovalCallbackOutput,
    ApprovalDecision,
    ApprovalDecisionType,
    PublicNoticeEntry,
    PublicSessionState,
    TextContentBlock,
)
from vibe.app_server.protocol import (
    CallbackResult,
    CallbackResultParams,
    PageRequest,
    SessionOptions,
    SessionReadParams,
    SessionResumeParams,
    SessionStartParams,
    TurnStartParams,
)
from vibe.core.config.harness_files import HarnessFilesManager


class _ScriptedCompletion:
    def __init__(self) -> None:
        self.next_program: str | None = None
        self.final_inputs: list[list[dict[str, Any]]] = []

    async def __call__(
        self,
        action: RustLLMCallAction,
        messages: list[Any],
        _tools: object,
        _config: object,
    ) -> RustCompletionSucceededEvent:
        program = self.next_program
        self.next_program = None
        parts: list[RustCompletionResultPart]
        if program is not None:
            parts = [
                RustCompletionResultToolCallPart(
                    id=f"program-{action.action_id}",
                    name="run_typescript",
                    arguments_json=json.dumps({"code": program}),
                )
            ]
            finish_reason = "tool_call"
        else:
            self.final_inputs.append([
                message.model_dump(mode="json", by_alias=True) for message in messages
            ])
            parts = [RustTextContentBlock(text="done")]
            finish_reason = "stop"
        return RustCompletionSucceededEvent(
            action_id=action.action_id,
            result=RustCompletionResult(
                parts=parts,
                finish_reason=finish_reason,
                usage=RustTokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            ),
        )


@pytest.mark.parametrize("profile", ["unix", "git_bash", "powershell"])
@pytest.mark.asyncio
async def test_vibe_enables_background_processes_for_every_host_shell_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: Literal["unix", "git_bash", "powershell"],
) -> None:
    """*Prepare*: A Vibe Unified Harness derivation for one supported host-shell profile.
    *Do*: Resolve the Core and local Runtime configuration.
    *Assert*: Background tools are enabled and use the same explicit shell authority.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setattr(
        "vibe.app_server._runtime._command_environment_mode", lambda: profile
    )
    process = HarnessProcess(HarnessFilesManager(sources=()), experimental_harness=True)

    # Do
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), auto_approve=False)
    )
    derivation = context.derive(UnifiedSessionSettings())

    # Assert
    assert derivation.core_config.settings.tools.background_processes.mode == "enabled"
    assert derivation.core_config.settings.tools.command_environment.mode == profile
    assert derivation.adapter_config.command_environment == profile
    assert derivation.adapter_config.process_authority == "host_shell"
    assert derivation.adapter_config.tool_modes["process.start"] == "ask"
    assert all(
        derivation.adapter_config.tool_modes[name] == "allow"
        for name in ("process.output", "process.write", "process.list", "process.stop")
    )


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX shell commands")
@pytest.mark.asyncio
async def test_vibe_background_process_survives_adapter_replacement_and_is_session_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A process-enabled Vibe Harness and scripted turns for two Sessions.
    *Do*: Start a process, replace its adapter, isolate another Session, then use all tools.
    *Assert*: The process survives replacement, remains isolated, and ends with one notification.
    """
    # Prepare
    scripted = _ScriptedCompletion()
    monkeypatch.setattr(
        "mistralai_vibe_local_harness.vibe._local_actions.execute_completion", scripted
    )
    host, storage_root = await _process_host(tmp_path, auto_approve=True)
    first = await host.start(_start_params(tmp_path, auto_approve=True))
    first_backend = first.backend

    # Do
    await _run_turn(
        first_backend,
        scripted,
        "start",
        """
async function main() {
  return tools.process.start({
    command: "printf ready; IFS= read -r value; printf got:$value; sleep 30"
  });
}
""",
    )
    first_store = UnifiedSessionStore(storage_root, first_backend.session_id)
    process_id = first_store.load().runtime_state.processes[0].process_id
    await first_backend.shutdown()
    replacement = (await host.resume(_resume_params(first_backend.session_id))).backend

    second = await host.start(_start_params(tmp_path, auto_approve=True))
    await _run_turn(
        second.backend,
        scripted,
        "list another session",
        "async function main() { return tools.process.list({}); }",
    )
    final_state = await _run_turn(
        replacement,
        scripted,
        "manage the process",
        f"""
async function main() {{
  const before = await tools.process.list({{}});
  const [output, written] = await Promise.all([
    tools.process.output({{processId: {json.dumps(process_id)}, waitMs: 3000}}),
    tools.process.write({{processId: {json.dumps(process_id)}, text: "hello\\n"}})
  ]);
  const after = await tools.process.output({{
    processId: {json.dumps(process_id)}, cursor: output.nextCursor, waitMs: 3000
  }});
  const stopped = await tools.process.stop({{processId: {json.dumps(process_id)}}});
  return {{before, output, written, after, stopped}};
}}
""",
    )

    # Assert
    assert r"{\"processes\":[]}" in _model_input_text(scripted.final_inputs[1])
    final_input = _model_input_text(scripted.final_inputs[-1])
    assert all(
        field in final_input
        for field in ("before", "output", "written", "after", "stopped")
    )
    stored = (
        UnifiedSessionStore(storage_root, replacement.session_id).load().runtime_state
    )
    assert stored.processes[0].process_id == process_id
    assert stored.processes[0].status == "stopped"
    assert [item.process_id for item in stored.submitted_process_notifications] == [
        process_id
    ]
    assert (
        UnifiedSessionStore(storage_root, second.backend.session_id)
        .load()
        .runtime_state.processes
        == []
    )
    assert not any(
        isinstance(entry, PublicNoticeEntry) for entry in final_state.history or []
    )
    transcript = ProcessOutputStore.recover(first_store.session_root, process_id).read(
        from_end=True, cursor=0, max_bytes=64_000
    )
    assert "got:hello" in transcript.output.decode()
    await second.backend.shutdown()
    await replacement.shutdown()
    await host.shutdown()


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX shell commands")
@pytest.mark.asyncio
async def test_vibe_background_process_approval_denial_never_starts_a_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A Vibe Harness whose shell policy requires approval.
    *Do*: Deny the callback raised by process.start.
    *Assert*: The turn completes with a denied effect and no process record.
    """
    # Prepare
    scripted = _ScriptedCompletion()
    monkeypatch.setattr(
        "mistralai_vibe_local_harness.vibe._local_actions.execute_completion", scripted
    )
    host, storage_root = await _process_host(tmp_path, auto_approve=False)
    backend = (await host.start(_start_params(tmp_path, auto_approve=False))).backend
    subscription = await backend.subscribe(
        SessionReadParams(session_id=backend.session_id, history=PageRequest(limit=100))
    )
    scripted.next_program = (
        "async function main() { return tools.process.start({command: 'sleep 30'}); }"
    )

    # Do
    started = await backend.start_turn(
        TurnStartParams(
            session_id=backend.session_id, message=[TextContentBlock(text="start")]
        )
    )
    assert started.after_response is not None
    started.after_response()
    completed = await _deny_callback_and_wait(subscription.events, backend)
    # Assert
    assert completed.turn.status == "completed"
    assert "tool_denied" in _model_input_text(scripted.final_inputs[-1])
    assert (
        UnifiedSessionStore(storage_root, backend.session_id)
        .load()
        .runtime_state.processes
        == []
    )
    await cast(Any, subscription.events).aclose()
    await backend.shutdown()
    await host.shutdown()


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX shell commands")
@pytest.mark.asyncio
async def test_vibe_host_shutdown_stops_a_running_background_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A Vibe Unified Session with a running background process.
    *Do*: Shut down the Vibe Session Host without explicitly stopping the process.
    *Assert*: Shutdown durably records a terminal state and notification acknowledgement.
    """
    # Prepare
    scripted = _ScriptedCompletion()
    monkeypatch.setattr(
        "mistralai_vibe_local_harness.vibe._local_actions.execute_completion", scripted
    )
    host, storage_root = await _process_host(tmp_path, auto_approve=True)
    backend = (await host.start(_start_params(tmp_path, auto_approve=True))).backend
    await _run_turn(
        backend,
        scripted,
        "start",
        "async function main() { return tools.process.start({command: 'sleep 30'}); }",
    )
    process_id = (
        UnifiedSessionStore(storage_root, backend.session_id)
        .load()
        .runtime_state.processes[0]
        .process_id
    )

    # Do
    await host.shutdown()

    # Assert
    stored = UnifiedSessionStore(storage_root, backend.session_id).load().runtime_state
    assert stored.processes[0].process_id == process_id
    assert stored.processes[0].status == "stopped"
    assert [item.process_id for item in stored.submitted_process_notifications] == [
        process_id
    ]


async def _process_host(
    tmp_path: Path, *, auto_approve: bool
) -> tuple[SessionBackendHost, Path]:
    process = HarnessProcess(HarnessFilesManager(sources=()), experimental_harness=True)
    options = SessionOptions(cwd=str(tmp_path), auto_approve=auto_approve)
    context = await process.build_unified_session_context(options)
    storage_root = tmp_path / "sessions"
    context = replace(context, storage_root=str(storage_root))

    async def build_context(
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: Any = "cli",
    ) -> UnifiedSessionContext:
        del options, require_api_key, entrypoint
        return context

    return adapt_harness_host(
        vibe_runtime.create_harness_host(), build_context
    ), storage_root


def _start_params(tmp_path: Path, *, auto_approve: bool) -> SessionStartParams:
    return SessionStartParams(
        agent_config=SessionOptions(cwd=str(tmp_path), auto_approve=auto_approve),
        history_limit=100,
    )


def _resume_params(session_id: str) -> SessionResumeParams:
    return SessionResumeParams(session_id=session_id, history_limit=100)


async def _run_turn(
    backend: SessionBackend, scripted: _ScriptedCompletion, prompt: str, program: str
) -> PublicSessionState:
    subscription = await backend.subscribe(
        SessionReadParams(session_id=backend.session_id, history=PageRequest(limit=100))
    )
    scripted.next_program = program
    result = await backend.start_turn(
        TurnStartParams(
            session_id=backend.session_id, message=[TextContentBlock(text=prompt)]
        )
    )
    assert result.after_response is not None
    result.after_response()
    await asyncio.wait_for(_wait_for_turn(subscription.events), timeout=15)
    await cast(Any, subscription.events).aclose()
    return (
        await backend.read(
            SessionReadParams(
                session_id=backend.session_id, history=PageRequest(limit=100)
            )
        )
    ).state


async def _wait_for_turn(events: AsyncIterator[Any]) -> TurnCompleted:
    async for envelope in events:
        if isinstance(envelope.event, TurnCompleted):
            return envelope.event
    raise AssertionError("event stream ended before turn completion")


async def _deny_callback_and_wait(
    events: AsyncIterator[Any], backend: SessionBackend
) -> TurnCompleted:
    async for envelope in events:
        if isinstance(envelope.event, CallbackRequested):
            await backend.respond_to_callback(
                CallbackResultParams(
                    session_id=backend.session_id,
                    result=CallbackResult(
                        callback_id=envelope.event.callback.callback_id,
                        output=ApprovalCallbackOutput(
                            decision=ApprovalDecision(type=ApprovalDecisionType.DENY)
                        ).model_dump(mode="json", by_alias=True),
                    ),
                )
            )
        elif isinstance(envelope.event, TurnCompleted):
            return envelope.event
    raise AssertionError("event stream ended before turn completion")


def _model_input_text(messages: list[dict[str, Any]]) -> str:
    return json.dumps(messages, sort_keys=True)
