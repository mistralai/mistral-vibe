from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
import json
from pathlib import Path
from typing import Any, cast

import pytest

pytest.importorskip("mistralai_vibe_local_harness.session_protocol")
vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")

from mistralai_vibe_local_harness.protocol import (
    RustCompletionResult,
    RustCompletionResultPart,
    RustCompletionResultToolCallPart,
    RustCompletionSucceededEvent,
    RustLLMCallAction,
    RustTextContentBlock,
    RustTokenUsage,
)
from vibe.app_server._admin_config import OrgConfigSource
from vibe.app_server._runtime import HarnessProcess
from vibe.app_server._session_backend_port import SessionBackend, SessionBackendHost
from vibe.app_server._unified_harness_backend_adapter import (
    UnifiedSessionContext,
    adapt_harness_host,
)
from vibe.app_server.events import TurnCompleted
from vibe.app_server.models import PublicSessionState, TextContentBlock
from vibe.app_server.protocol import (
    PageRequest,
    SessionOptions,
    SessionReadParams,
    SessionStartParams,
    TurnStartParams,
)
from vibe.core.config.harness_files import HarnessFilesManager

# A `run_typescript` program is one tool call, so a direct-only todo tool would leave
# the list frozen for its whole duration: read, write and read again inside one program.
_TODO_PROGRAM = """
async function main() {
  const before = await tools.vibe.todo({ action: "read" });
  await tools.vibe.todo({
    action: "write",
    todos: [
      { id: "1", content: "port the narrator", status: "in_progress" },
      { id: "2", content: "pin the plan row", status: "pending" },
    ],
  });
  const after = await tools.vibe.todo({ action: "read" });
  return {
    beforeCount: before.todos.length,
    afterContents: after.todos.map((todo) => todo.content),
  };
}
"""


class _ScriptedCompletion:
    def __init__(self) -> None:
        self.next_program: str | None = None
        self.next_call: RustCompletionResultToolCallPart | None = None
        self.final_inputs: list[list[dict[str, Any]]] = []

    async def __call__(
        self,
        action: RustLLMCallAction,
        messages: list[Any],
        _tools: object,
        _config: object,
        *,
        retry_sink: object | None = None,
        delta_sink: object | None = None,
    ) -> RustCompletionSucceededEvent:
        program = self.next_program
        self.next_program = None
        call = self.next_call
        self.next_call = None
        parts: list[RustCompletionResultPart]
        if call is not None:
            parts = [call]
            finish_reason = "tool_call"
        elif program is not None:
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


@pytest.mark.asyncio
async def test_a_typescript_program_reads_and_writes_the_todo_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A Vibe Unified Harness session and a scripted `run_typescript` turn.
    *Do*: Run a program that reads the todos, writes two, and reads them back.
    *Assert*: The program saw an empty list first, then its own write, and the
    session history carries the settled todo effect the pinned row is fed from.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    scripted = _ScriptedCompletion()
    monkeypatch.setattr(
        "mistralai_vibe_local_harness.vibe._local_actions.execute_completion", scripted
    )
    host = await _todo_host(tmp_path)
    session = await host.start(_start_params(tmp_path))

    # Do
    state = await _run_turn(session.backend, scripted, "plan the port", _TODO_PROGRAM)

    # Assert
    follow_up = json.dumps(scripted.final_inputs[-1])
    # The list was empty before the program's own write and held both items after,
    # so `tools.vibe.todo` is reachable and effective mid-program.
    assert '"beforeCount\\":0' in follow_up or '"beforeCount": 0' in follow_up
    assert "port the narrator" in follow_up
    assert "pin the plan row" in follow_up
    assert _settled_todo_contents(state) == ["port the narrator", "pin the plan row"]


@pytest.mark.parametrize("tool_name", ["grep", "vibe.grep", "file_system.grep"])
@pytest.mark.asyncio
async def test_removed_grep_cannot_return_sensitive_file_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    """A model's obsolete Grep call fails through the real session, even in Auto Approve."""
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    search_path = tmp_path / "search"
    search_path.mkdir()
    (search_path / ".env").write_text("VALUE=SYNTHETIC_REMOVED_GREP\n")
    (search_path / "public.txt").write_text("VALUE=public\n")
    scripted = _ScriptedCompletion()
    scripted.next_call = RustCompletionResultToolCallPart(
        id="removed-grep",
        name=tool_name,
        arguments_json=json.dumps({"pattern": "=", "path": "search"}),
    )
    monkeypatch.setattr(
        "mistralai_vibe_local_harness.vibe._local_actions.execute_completion", scripted
    )
    host = await _todo_host(tmp_path)
    try:
        session = await host.start(_start_params(tmp_path))
        state = await _run_turn(session.backend, scripted, "search assignments")
        follow_up = json.dumps(scripted.final_inputs[-1])
        assert "SYNTHETIC_REMOVED_GREP" not in follow_up
        assert "SYNTHETIC_REMOVED_GREP" not in state.model_dump_json()
        response = next(
            message
            for message in scripted.final_inputs[-1]
            if message.get("tool_call_id") == "removed-grep"
        )
        assert response["outcome"] == "failure"
        result = json.loads(response["content"][0]["text"])["result"]
        assert result == {
            "type": "error",
            "error": {
                "name": "InvalidToolCall",
                "message": f'unsupported top-level tool "{tool_name}"',
            },
        }
    finally:
        await host.shutdown()


@pytest.mark.asyncio
async def test_a_program_cannot_call_the_removed_grep_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    (tmp_path / ".env").write_text("VALUE=SYNTHETIC_PROGRAM_GREP\n")
    scripted = _ScriptedCompletion()
    monkeypatch.setattr(
        "mistralai_vibe_local_harness.vibe._local_actions.execute_completion", scripted
    )
    host = await _todo_host(tmp_path)
    try:
        session = await host.start(_start_params(tmp_path))
        program = """
async function main() {
  try {
    return await tools.file_system.grep({ pattern: "=", path: "." });
  } catch (error) {
    return { rejected: true, message: String(error) };
  }
}
"""
        state = await _run_turn(
            session.backend, scripted, "search assignments", program
        )
        follow_up = json.dumps(scripted.final_inputs[-1])
        assert "SYNTHETIC_PROGRAM_GREP" not in follow_up
        assert "SYNTHETIC_PROGRAM_GREP" not in state.model_dump_json()
        response = next(
            message
            for message in scripted.final_inputs[-1]
            if message.get("role") == "tool" and message.get("name") == "run_typescript"
        )
        assert response["outcome"] == "success"
        assert json.loads(response["content"][0]["text"]) == {
            "rejected": True,
            "message": "TypeError: tools.file_system.grep is not a function",
        }
    finally:
        await host.shutdown()


@pytest.mark.parametrize("path", ["--help", "--pre=removed-grep-sentinel", "-n"])
@pytest.mark.asyncio
async def test_removed_grep_rejects_option_shaped_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Obsolete Grep calls never reach a backend, even with Auto Approve."""
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    scripted = _ScriptedCompletion()
    scripted.next_call = RustCompletionResultToolCallPart(
        id="removed-grep-option",
        name="grep",
        arguments_json=json.dumps({"pattern": ".", "path": path}),
    )
    monkeypatch.setattr(
        "mistralai_vibe_local_harness.vibe._local_actions.execute_completion", scripted
    )
    host = await _todo_host(tmp_path)
    try:
        session = await host.start(_start_params(tmp_path))
        await _run_turn(session.backend, scripted, "search the requested path")
        response = next(
            message
            for message in scripted.final_inputs[-1]
            if message.get("tool_call_id") == "removed-grep-option"
        )
        assert response["outcome"] == "failure"
        result = json.loads(response["content"][0]["text"])["result"]
        assert result == {
            "type": "error",
            "error": {
                "name": "InvalidToolCall",
                "message": 'unsupported top-level tool "grep"',
            },
        }
    finally:
        await host.shutdown()


def _settled_todo_contents(state: PublicSessionState) -> list[str]:
    for entry in reversed(state.history or []):
        raw = entry.model_dump(mode="json", by_alias=True)
        if raw.get("type") != "effect":
            continue
        if (raw.get("detail") or {}).get("kind") != "todo":
            continue
        todos = ((raw.get("state") or {}).get("output") or {}).get("todos")
        if todos is not None:
            return [todo["content"] for todo in todos]
    raise AssertionError("no settled todo effect in history")


async def _todo_host(tmp_path: Path) -> SessionBackendHost:
    process = HarnessProcess(HarnessFilesManager(sources=()))
    options = SessionOptions(cwd=str(tmp_path), auto_approve=True)
    context = await process.build_unified_session_context(options)
    context = replace(context, storage_root=str(tmp_path / "sessions"))

    async def build_context(
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: Any = "cli",
        org_config: OrgConfigSource,
    ) -> UnifiedSessionContext:
        del options, require_api_key, entrypoint, org_config
        return context

    return adapt_harness_host(vibe_runtime.create_harness_host(), build_context)


def _start_params(tmp_path: Path) -> SessionStartParams:
    return SessionStartParams(
        agent_config=SessionOptions(cwd=str(tmp_path), auto_approve=True),
        history_limit=100,
    )


async def _run_turn(
    backend: SessionBackend,
    scripted: _ScriptedCompletion,
    prompt: str,
    program: str | None = None,
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
    await asyncio.wait_for(_wait_for_turn(subscription.events), timeout=30)
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
