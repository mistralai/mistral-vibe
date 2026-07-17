from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys
from typing import Any

import pytest
import tomli_w

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_tool import FakeTool, FakeToolArgs
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import SessionLoggingConfig
from vibe.core.hooks._handler import HookOutputError, _parse_structured_response
from vibe.core.hooks.config import (
    HookConfigResult,
    _load_hooks_file,
    load_hooks_from_fs,
)
from vibe.core.hooks.executor import HookExecutor
from vibe.core.hooks.manager import HooksManager
from vibe.core.hooks.models import (
    HookConfig,
    HookEndEvent,
    HookEvent,
    HookMessageSeverity,
    HookRunEndEvent,
    HookRunStartEvent,
    HookSessionContext,
    HookStartEvent,
    HookStructuredResponse,
    HookTextReplacement,
    HookToolDenial,
    HookToolInputRewrite,
    HookType,
    HookUserMessage,
    PostAgentInvocation,
    PostToolInvocation,
    build_invocation,
)
from vibe.core.types import AssistantEvent, FunctionCall, ToolCall, ToolResultEvent

_AnyHookYield = (
    HookEvent
    | HookUserMessage
    | HookToolDenial
    | HookToolInputRewrite
    | HookTextReplacement
)


def _run(
    handler: HooksManager, hook_type: HookType, ctx: HookSessionContext, **kwargs: Any
) -> Any:
    """Test convenience: build the right invocation subclass and pipe it
    to ``HooksManager.run``. The manager itself only knows about
    invocations — the per-type kwargs (``tool_name`` / ``tool_input`` /
    ``initial_text`` mapped to ``tool_output_text`` / …) are flattened
    here so tests stay readable.
    """
    initial_text = kwargs.pop("initial_text", "")
    return handler.run(
        build_invocation(hook_type, ctx, tool_output_text=initial_text, **kwargs)
    )


async def _drain_post_tool_chain(
    handler: HooksManager, ctx: HookSessionContext, **kwargs: object
) -> tuple[str, list[_AnyHookYield]]:
    final_text = str(kwargs.get("initial_text", ""))
    events: list[_AnyHookYield] = []
    async for ev in _run(handler, HookType.POST_TOOL, ctx, **kwargs):
        if isinstance(ev, HookTextReplacement):
            final_text = ev.text
        else:
            events.append(ev)
    return final_text, events


@pytest.fixture
def sample_invocation() -> PostAgentInvocation:
    return PostAgentInvocation(
        session_id="test-session", transcript_path="", cwd=str(Path.cwd())
    )


@pytest.fixture
def ctx() -> HookSessionContext:
    return HookSessionContext(
        session_id="sess", transcript_path="", cwd=str(Path.cwd())
    )


def _write_hooks_toml(path: Path, hooks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        tomli_w.dump({"hooks": hooks}, f)


def _make_hook(
    name: str = "test-hook",
    command: str = "echo ok",
    timeout: float = 60.0,
    type: HookType = HookType.POST_AGENT,
    match: str | None = None,
) -> HookConfig:
    return HookConfig(
        name=name, type=type, command=command, timeout=timeout, match=match
    )


def _make_tool_hook(
    name: str,
    command: str,
    *,
    type: HookType,
    match: str | None = None,
    timeout: float | None = None,
    strict: bool = False,
) -> HookConfig:
    return HookConfig(
        name=name,
        type=type,
        command=command,
        match=match,
        timeout=timeout,
        strict=strict,
    )


def _emit_cmd(payload: dict[str, Any]) -> str:
    """Build a shell command that prints ``payload`` as JSON to stdout.

    Uses Python (over ``printf``) so embedded quotes / shell metacharacters
    in field values are not a footgun, and ``shlex.quote`` to give the
    shell a single safe argument to pass to ``-c``.
    """
    body = f"import sys; sys.stdout.write({json.dumps(payload)!r})"
    return f"{sys.executable} -c {shlex.quote(body)}"


def _deny_cmd(reason: str = "") -> str:
    return _emit_cmd({"decision": "deny", "reason": reason})


class TestConfigLoading:
    def test_load_from_global_file(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "lint", "type": HookType.POST_AGENT, "command": "echo lint"}],
        )
        result = load_hooks_from_fs()
        assert len(result.hooks) == 1
        assert result.hooks[0].name == "lint"
        assert result.issues == []

    def test_load_from_both_global_and_project(
        self, config_dir: Path, tmp_working_directory: Path
    ) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "global-hook", "type": "post_agent", "command": "echo global"}],
        )
        project_vibe = tmp_working_directory / ".vibe"
        _write_hooks_toml(
            project_vibe / "hooks.toml",
            [{"name": "project-hook", "type": "post_agent", "command": "echo project"}],
        )
        from vibe.core.trusted_folders import trusted_folders_manager

        trusted_folders_manager.add_trusted(tmp_working_directory)

        result = load_hooks_from_fs()
        assert len(result.hooks) == 2
        names = [h.name for h in result.hooks]
        # Project hooks are loaded first.
        assert names == ["project-hook", "global-hook"]

    def test_project_file_skipped_when_untrusted(
        self, tmp_working_directory: Path
    ) -> None:
        project_vibe = tmp_working_directory / ".vibe"
        _write_hooks_toml(
            project_vibe / "hooks.toml",
            [{"name": "sneaky-hook", "type": "post_agent", "command": "echo sneaky"}],
        )
        result = load_hooks_from_fs()
        assert not any(h.name == "sneaky-hook" for h in result.hooks)

    def test_duplicate_hook_name_project_wins(
        self, config_dir: Path, tmp_working_directory: Path
    ) -> None:
        # Project file loads first; the user-global duplicate is flagged.
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "dup-hook", "type": "post_agent", "command": "echo global"}],
        )
        project_vibe = tmp_working_directory / ".vibe"
        _write_hooks_toml(
            project_vibe / "hooks.toml",
            [{"name": "dup-hook", "type": "post_agent", "command": "echo project"}],
        )
        from vibe.core.trusted_folders import trusted_folders_manager

        trusted_folders_manager.add_trusted(tmp_working_directory)

        result = load_hooks_from_fs()
        assert len(result.hooks) == 1
        assert result.hooks[0].command == "echo project"
        assert any("Duplicate" in i.message for i in result.issues)

    def test_toml_parse_error_reported(self, config_dir: Path) -> None:
        hooks_file = config_dir / "hooks.toml"
        hooks_file.write_text("this is not valid toml [[[", encoding="utf-8")
        result = load_hooks_from_fs()
        assert result.hooks == []
        assert len(result.issues) == 1
        assert (
            "parse" in result.issues[0].message.lower()
            or "Failed" in result.issues[0].message
        )

    def test_validation_error_reported(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "bad", "type": "InvalidType", "command": "echo"}],
        )
        result = load_hooks_from_fs()
        assert result.hooks == []
        assert len(result.issues) == 1

    def test_missing_command_reported(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml", [{"name": "no-cmd", "type": "post_agent"}]
        )
        result = load_hooks_from_fs()
        assert result.hooks == []
        assert len(result.issues) == 1

    def test_empty_command_reported(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "empty-cmd", "type": "post_agent", "command": "   "}],
        )
        result = load_hooks_from_fs()
        assert result.hooks == []
        assert len(result.issues) == 1

    def test_default_timeout_is_uniform(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [
                {"name": "p", "type": "post_agent", "command": "echo ok"},
                {"name": "b", "type": "pre_tool", "command": "echo ok"},
                {"name": "a", "type": "post_tool", "command": "echo ok"},
            ],
        )
        result = load_hooks_from_fs()
        assert all(h.timeout == 60.0 for h in result.hooks)

    def test_explicit_timeout_overrides_default(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "b", "type": "pre_tool", "command": "echo ok", "timeout": 12.5}],
        )
        result = load_hooks_from_fs()
        assert result.hooks[0].timeout == 12.5

    def test_match_field_on_tool_hooks(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [
                {
                    "name": "b",
                    "type": "pre_tool",
                    "command": "echo ok",
                    "match": "bash",
                },
                {
                    "name": "a",
                    "type": "post_tool",
                    "command": "echo ok",
                    "match": "re:read_.*",
                },
            ],
        )
        result = load_hooks_from_fs()
        assert result.hooks[0].match == "bash"
        assert result.hooks[1].match == "re:read_.*"

    def test_match_field_rejected_on_post_agent(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [
                {
                    "name": "h",
                    "type": "post_agent",
                    "command": "echo ok",
                    "match": "bash",
                }
            ],
        )
        result = load_hooks_from_fs()
        assert result.hooks == []
        assert any("match" in i.message for i in result.issues)

    def test_empty_match_rejected(self, config_dir: Path) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "h", "type": "pre_tool", "command": "echo ok", "match": "   "}],
        )
        result = load_hooks_from_fs()
        assert result.hooks == []

    def test_nonexistent_file_returns_empty(self, tmp_path: Path) -> None:
        result = _load_hooks_file(tmp_path / "missing.toml")
        assert result.hooks == []
        assert result.issues == []


class TestHookExecutor:
    @pytest.mark.asyncio
    async def test_exit_0_success(self, sample_invocation: PostAgentInvocation) -> None:
        hook = _make_hook(command="echo success")
        result = await HookExecutor().run(hook, sample_invocation)
        assert result.exit_code == 0
        assert result.stdout == "success"
        assert not result.timed_out

    @pytest.mark.asyncio
    async def test_nonzero_exit_passes_through(
        self, sample_invocation: PostAgentInvocation
    ) -> None:
        # The executor is a thin wrapper around the subprocess — it does not
        # interpret exit codes itself. Any non-zero value is forwarded so the
        # manager can decide what to do.
        hook = _make_hook(command="echo 'oops'; exit 1")
        result = await HookExecutor().run(hook, sample_invocation)
        assert result.exit_code == 1
        assert "oops" in result.stdout

    @pytest.mark.asyncio
    async def test_timeout(self, sample_invocation: PostAgentInvocation) -> None:
        hook = _make_hook(command="sleep 60", timeout=0.5)
        result = await HookExecutor().run(hook, sample_invocation)
        assert result.timed_out
        assert result.exit_code is None

    @pytest.mark.asyncio
    async def test_timeout_after_stdio_closed(
        self, sample_invocation: PostAgentInvocation
    ) -> None:
        # A hook that closes stdout/stderr but keeps running must still be
        # killed by the timeout. Before the fix, process.wait() had no
        # timeout so the session would hang indefinitely.
        script = (
            f'{sys.executable} -c "'
            "import sys, time; "
            "sys.stdout.close(); sys.stderr.close(); "
            "time.sleep(60)"
            '"'
        )
        hook = _make_hook(command=script, timeout=0.5)
        result = await HookExecutor().run(hook, sample_invocation)
        assert result.timed_out
        assert result.exit_code is None

    @pytest.mark.asyncio
    async def test_stderr_captured_separately(
        self, sample_invocation: PostAgentInvocation
    ) -> None:
        hook = _make_hook(command="echo out; echo err >&2")
        result = await HookExecutor().run(hook, sample_invocation)
        assert result.exit_code == 0
        assert result.stdout == "out"
        assert result.stderr == "err"

    @pytest.mark.asyncio
    async def test_stdin_json_received(
        self, sample_invocation: PostAgentInvocation
    ) -> None:
        hook = _make_hook(
            command=f"{sys.executable} -c \"import sys,json; d=json.load(sys.stdin); print(d['session_id'])\""
        )
        result = await HookExecutor().run(hook, sample_invocation)
        assert result.exit_code == 0
        assert result.stdout == "test-session"

    @pytest.mark.asyncio
    async def test_large_stdin_when_child_closes_pipe_does_not_crash(self) -> None:
        command = f"{sys.executable} -c \"import sys; sys.stdin.close(); print('ok')\""
        hook = _make_hook(command=command, type=HookType.POST_TOOL)
        invocation = PostToolInvocation(
            session_id="test-session",
            transcript_path="",
            cwd=str(Path.cwd()),
            tool_name="tool",
            tool_call_id="call-1",
            tool_input={},
            tool_status="success",
            tool_output=None,
            tool_output_text="x" * 500_000,
            tool_error=None,
            duration_ms=1.0,
        )
        result = await HookExecutor().run(hook, invocation)
        assert result.exit_code == 0
        assert result.stdout == "ok"
        assert not result.timed_out

    @pytest.mark.asyncio
    async def test_spawn_failure_message_goes_to_stderr(
        self, sample_invocation: PostAgentInvocation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError("nope")

        monkeypatch.setattr("asyncio.create_subprocess_shell", _raise)
        result = await HookExecutor().run(_make_hook(), sample_invocation)
        assert result.exit_code == 1
        assert result.stdout == ""
        assert "Failed to start" in result.stderr
        assert "nope" in result.stderr


class TestPostAgentHook:
    @pytest.mark.asyncio
    async def test_exit_0_emits_start_and_end(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([_make_hook(command="echo ok")])
        events: list[_AnyHookYield] = []
        async for ev in _run(handler, HookType.POST_AGENT, ctx):
            events.append(ev)

        event_types = [type(e).__name__ for e in events]
        assert "HookRunStartEvent" in event_types
        assert "HookStartEvent" in event_types
        assert "HookEndEvent" in event_types
        assert "HookRunEndEvent" in event_types
        assert not any(isinstance(e, HookUserMessage) for e in events)

    @pytest.mark.asyncio
    async def test_decision_deny_injects_user_message(
        self, ctx: HookSessionContext
    ) -> None:
        handler = HooksManager([_make_hook(command=_deny_cmd("fix it"))])
        events: list[_AnyHookYield] = []
        async for ev in _run(handler, HookType.POST_AGENT, ctx):
            events.append(ev)

        retry_msgs = [e for e in events if isinstance(e, HookUserMessage)]
        assert len(retry_msgs) == 1
        assert retry_msgs[0].content == "fix it"

        end_msgs = [
            e for e in events if isinstance(e, HookEndEvent) and e.content is not None
        ]
        assert any("retrying" in m.content.lower() for m in end_msgs if m.content)
        assert not any("fix it" in (m.content or "") for m in end_msgs)

    @pytest.mark.asyncio
    async def test_decision_deny_with_no_reason_injects_empty(
        self, ctx: HookSessionContext
    ) -> None:
        # decision=deny with reason missing/empty injects an empty user
        # message — the hook explicitly asked for a retry with no guidance.
        handler = HooksManager([_make_hook(command=_deny_cmd())])
        events: list[_AnyHookYield] = []
        async for ev in _run(handler, HookType.POST_AGENT, ctx):
            events.append(ev)

        retry_msgs = [e for e in events if isinstance(e, HookUserMessage)]
        assert len(retry_msgs) == 1
        assert retry_msgs[0].content == ""

    @pytest.mark.asyncio
    async def test_max_retry_limit(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([_make_hook(command=_deny_cmd("retry"))])

        for _ in range(3):
            events = [ev async for ev in _run(handler, HookType.POST_AGENT, ctx)]
            assert any(isinstance(e, HookUserMessage) for e in events)

        events = [ev async for ev in _run(handler, HookType.POST_AGENT, ctx)]
        assert not any(isinstance(e, HookUserMessage) for e in events)
        error_events = [
            e
            for e in events
            if isinstance(e, HookEndEvent)
            and e.content
            and "exhausted" in e.content.lower()
        ]
        assert len(error_events) == 1

    @pytest.mark.asyncio
    async def test_warning_prefers_stderr_on_nonzero_exit(
        self, ctx: HookSessionContext
    ) -> None:
        # Stderr is the conventional channel for shell diagnostics, and is
        # now preferred over stdout (which is reserved for the JSON response
        # and may be empty / garbage on a crash).
        handler = HooksManager([
            _make_hook(command="echo stdout-msg; echo stderr-msg >&2; exit 1")
        ])
        events = [ev async for ev in _run(handler, HookType.POST_AGENT, ctx)]

        warnings = [
            e
            for e in events
            if isinstance(e, HookEndEvent) and e.status == HookMessageSeverity.WARNING
        ]
        assert len(warnings) == 1
        assert warnings[0].content == "stderr-msg"

    @pytest.mark.asyncio
    async def test_warning_falls_back_to_stdout(self, ctx: HookSessionContext) -> None:
        # When stderr is empty, stdout is still used as a fallback.
        handler = HooksManager([_make_hook(command="echo only-stdout; exit 1")])
        events = [ev async for ev in _run(handler, HookType.POST_AGENT, ctx)]

        warnings = [
            e
            for e in events
            if isinstance(e, HookEndEvent) and e.status == HookMessageSeverity.WARNING
        ]
        assert len(warnings) == 1
        assert warnings[0].content == "only-stdout"

    @pytest.mark.asyncio
    async def test_timeout_emits_warning(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([_make_hook(command="sleep 60", timeout=0.5)])
        events = [ev async for ev in _run(handler, HookType.POST_AGENT, ctx)]

        warnings = [
            e
            for e in events
            if isinstance(e, HookEndEvent) and e.status == HookMessageSeverity.WARNING
        ]
        assert len(warnings) == 1
        assert warnings[0].content and "Timed out" in warnings[0].content


class TestPreToolHook:
    @pytest.mark.asyncio
    async def test_no_hooks_no_events(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([])
        events = [
            ev
            async for ev in _run(
                handler,
                HookType.PRE_TOOL,
                ctx,
                tool_name="bash",
                tool_call_id="tc1",
                tool_input={},
            )
        ]
        assert events == []

    @pytest.mark.asyncio
    async def test_matcher_filters_non_matching(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([
            _make_tool_hook(
                "guard", _deny_cmd("nope"), type=HookType.PRE_TOOL, match="bash"
            )
        ])
        events = [
            ev
            async for ev in _run(
                handler,
                HookType.PRE_TOOL,
                ctx,
                tool_name="read_file",
                tool_call_id="tc1",
                tool_input={},
            )
        ]
        # Non-matching tool: no hooks, no events at all.
        assert events == []

    @pytest.mark.asyncio
    async def test_exit_0_allows_tool(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([
            _make_tool_hook("audit", "echo ok", type=HookType.PRE_TOOL)
        ])
        events = [
            ev
            async for ev in _run(
                handler,
                HookType.PRE_TOOL,
                ctx,
                tool_name="bash",
                tool_call_id="tc1",
                tool_input={"command": "ls"},
            )
        ]
        assert not any(isinstance(e, HookToolDenial) for e in events)
        assert any(isinstance(e, HookRunStartEvent) for e in events)
        assert any(isinstance(e, HookRunEndEvent) for e in events)

    @pytest.mark.asyncio
    async def test_decision_deny_with_reason(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([
            _make_tool_hook("guard", _deny_cmd("no rm -rf"), type=HookType.PRE_TOOL)
        ])
        events = [
            ev
            async for ev in _run(
                handler,
                HookType.PRE_TOOL,
                ctx,
                tool_name="bash",
                tool_call_id="tc1",
                tool_input={"command": "rm -rf /"},
            )
        ]
        denials = [e for e in events if isinstance(e, HookToolDenial)]
        assert len(denials) == 1
        assert denials[0].hook_name == "guard"
        assert denials[0].content == "no rm -rf"

    @pytest.mark.asyncio
    async def test_decision_deny_with_no_reason(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([
            _make_tool_hook("guard", _deny_cmd(), type=HookType.PRE_TOOL)
        ])
        events = [
            ev
            async for ev in _run(
                handler,
                HookType.PRE_TOOL,
                ctx,
                tool_name="bash",
                tool_call_id="tc1",
                tool_input={},
            )
        ]
        denials = [e for e in events if isinstance(e, HookToolDenial)]
        assert len(denials) == 1
        assert denials[0].content == ""

    @pytest.mark.asyncio
    async def test_first_deny_wins(self, ctx: HookSessionContext) -> None:
        # Two hooks both match; the first denies, the second must not run.
        handler = HooksManager([
            _make_tool_hook("first", _deny_cmd("first deny"), type=HookType.PRE_TOOL),
            _make_tool_hook(
                "second", _deny_cmd("second should not run"), type=HookType.PRE_TOOL
            ),
        ])
        events = [
            ev
            async for ev in _run(
                handler,
                HookType.PRE_TOOL,
                ctx,
                tool_name="bash",
                tool_call_id="tc1",
                tool_input={},
            )
        ]
        denials = [e for e in events if isinstance(e, HookToolDenial)]
        assert len(denials) == 1
        assert denials[0].hook_name == "first"
        start_events = [e for e in events if isinstance(e, HookStartEvent)]
        assert [e.hook_name for e in start_events] == ["first"]

    @pytest.mark.asyncio
    async def test_spawn_failure_is_fail_open(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([
            _make_tool_hook(
                "broken", "/nonexistent/hook/binary", type=HookType.PRE_TOOL
            )
        ])
        events = [
            ev
            async for ev in _run(
                handler,
                HookType.PRE_TOOL,
                ctx,
                tool_name="bash",
                tool_call_id="tc1",
                tool_input={},
            )
        ]
        denials = [e for e in events if isinstance(e, HookToolDenial)]
        assert denials == []  # fail-open: no deny on spawn failure
        warnings = [
            e
            for e in events
            if isinstance(e, HookEndEvent) and e.status == HookMessageSeverity.WARNING
        ]
        assert len(warnings) == 1

    @pytest.mark.asyncio
    async def test_strict_failure_denies(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([
            _make_tool_hook("guard", "exit 1", type=HookType.PRE_TOOL, strict=True),
            _make_tool_hook("second", "echo ok", type=HookType.PRE_TOOL),
        ])
        events = [
            ev
            async for ev in _run(
                handler,
                HookType.PRE_TOOL,
                ctx,
                tool_name="bash",
                tool_call_id="tc1",
                tool_input={},
            )
        ]
        denials = [e for e in events if isinstance(e, HookToolDenial)]
        assert len(denials) == 1
        assert denials[0].hook_name == "guard"
        errors = [
            e
            for e in events
            if isinstance(e, HookEndEvent) and e.status == HookMessageSeverity.ERROR
        ]
        assert any("strict" in (e.content or "") for e in errors)
        # Second hook must not have started
        starts = [e for e in events if isinstance(e, HookStartEvent)]
        assert [e.hook_name for e in starts] == ["guard"]

    @pytest.mark.asyncio
    async def test_strict_timeout_denies(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([
            _make_tool_hook(
                "slow", "sleep 10", type=HookType.PRE_TOOL, timeout=0.1, strict=True
            )
        ])
        events = [
            ev
            async for ev in _run(
                handler,
                HookType.PRE_TOOL,
                ctx,
                tool_name="bash",
                tool_call_id="tc1",
                tool_input={},
            )
        ]
        denials = [e for e in events if isinstance(e, HookToolDenial)]
        assert len(denials) == 1
        assert denials[0].hook_name == "slow"


class TestPostToolHook:
    @pytest.mark.asyncio
    async def test_no_hooks_returns_initial_text(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([])
        final_text, events = await _drain_post_tool_chain(
            handler,
            ctx,
            tool_name="bash",
            tool_call_id="tc1",
            tool_input={},
            tool_status="success",
            tool_output={"result": "ok"},
            tool_error=None,
            duration_ms=10.0,
            initial_text="ok",
        )
        assert final_text == "ok"
        assert events == []

    @pytest.mark.asyncio
    async def test_exit_0_passthrough(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([
            _make_tool_hook("audit", "echo ok", type=HookType.POST_TOOL)
        ])
        final_text, events = await _drain_post_tool_chain(
            handler,
            ctx,
            tool_name="bash",
            tool_call_id="tc1",
            tool_input={},
            tool_status="success",
            tool_output={"r": 1},
            tool_error=None,
            duration_ms=10.0,
            initial_text="original",
        )
        assert final_text == "original"
        assert any(isinstance(e, HookRunStartEvent) for e in events)

    @pytest.mark.asyncio
    async def test_decision_deny_replaces_text(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([
            _make_tool_hook("redact", _deny_cmd("REDACTED"), type=HookType.POST_TOOL)
        ])
        final_text, _events = await _drain_post_tool_chain(
            handler,
            ctx,
            tool_name="bash",
            tool_call_id="tc1",
            tool_input={},
            tool_status="success",
            tool_output={"r": 1},
            tool_error=None,
            duration_ms=10.0,
            initial_text="sensitive data",
        )
        assert final_text == "REDACTED"

    @pytest.mark.asyncio
    async def test_decision_deny_no_reason_replaces_with_empty(
        self, ctx: HookSessionContext
    ) -> None:
        handler = HooksManager([
            _make_tool_hook("silence", _deny_cmd(), type=HookType.POST_TOOL)
        ])
        final_text, _events = await _drain_post_tool_chain(
            handler,
            ctx,
            tool_name="bash",
            tool_call_id="tc1",
            tool_input={},
            tool_status="success",
            tool_output={"r": 1},
            tool_error=None,
            duration_ms=10.0,
            initial_text="something",
        )
        assert final_text == ""

    @pytest.mark.asyncio
    async def test_additional_context_appends_to_text(
        self, ctx: HookSessionContext
    ) -> None:
        # hook_specific_output.additional_context is appended (not replaced)
        # to the current tool_output_text.
        payload = {"hook_specific_output": {"additional_context": "[redacted 1 key]"}}
        handler = HooksManager([
            _make_tool_hook("audit", _emit_cmd(payload), type=HookType.POST_TOOL)
        ])
        final_text, _events = await _drain_post_tool_chain(
            handler,
            ctx,
            tool_name="bash",
            tool_call_id="tc1",
            tool_input={},
            tool_status="success",
            tool_output={"r": 1},
            tool_error=None,
            duration_ms=10.0,
            initial_text="original output",
        )
        assert final_text == "original output\n[redacted 1 key]"

    @pytest.mark.asyncio
    async def test_deny_plus_additional_context_combines(
        self, ctx: HookSessionContext
    ) -> None:
        # decision=deny replaces with reason, then additional_context appends
        # to the replacement (Gemini-style combined semantics).
        payload = {
            "decision": "deny",
            "reason": "REDACTED",
            "hook_specific_output": {"additional_context": "(2 secrets stripped)"},
        }
        handler = HooksManager([
            _make_tool_hook("redact", _emit_cmd(payload), type=HookType.POST_TOOL)
        ])
        final_text, _events = await _drain_post_tool_chain(
            handler,
            ctx,
            tool_name="bash",
            tool_call_id="tc1",
            tool_input={},
            tool_status="success",
            tool_output={"r": 1},
            tool_error=None,
            duration_ms=10.0,
            initial_text="sensitive data",
        )
        assert final_text == "REDACTED\n(2 secrets stripped)"

    @pytest.mark.asyncio
    async def test_pipeline_composes_left_to_right(
        self, ctx: HookSessionContext
    ) -> None:
        # First hook replaces with "piped". Second hook reads stdin and emits
        # a JSON deny whose reason is the prior text uppercased.
        upper_script = (
            f'{sys.executable} -c "'
            "import sys,json; "
            "d=json.load(sys.stdin); "
            "sys.stdout.write(json.dumps("
            "{'decision':'deny','reason': d['tool_output_text'].upper()}"
            "))"
            '"'
        )
        handler = HooksManager([
            _make_tool_hook("first", _deny_cmd("piped"), type=HookType.POST_TOOL),
            _make_tool_hook("second", upper_script, type=HookType.POST_TOOL),
        ])
        final_text, _events = await _drain_post_tool_chain(
            handler,
            ctx,
            tool_name="bash",
            tool_call_id="tc1",
            tool_input={},
            tool_status="success",
            tool_output={"r": 1},
            tool_error=None,
            duration_ms=10.0,
            initial_text="initial",
        )
        assert final_text == "PIPED"

    @pytest.mark.asyncio
    async def test_decision_deny_on_failure_status_still_replaces(
        self, ctx: HookSessionContext
    ) -> None:
        handler = HooksManager([
            _make_tool_hook(
                "rescue", _deny_cmd("synthetic recovery"), type=HookType.POST_TOOL
            )
        ])
        final_text, _events = await _drain_post_tool_chain(
            handler,
            ctx,
            tool_name="bash",
            tool_call_id="tc1",
            tool_input={},
            tool_status="failure",
            tool_output=None,
            tool_error="boom",
            duration_ms=0.0,
            initial_text="<tool_error>boom</tool_error>",
        )
        assert final_text == "synthetic recovery"

    @pytest.mark.asyncio
    async def test_invocation_includes_status_and_output(
        self, ctx: HookSessionContext
    ) -> None:
        # Hook script asserts the invocation payload contains the expected
        # fields and writes a JSON deny whose reason confirms success.
        script = (
            f'{sys.executable} -c "'
            "import sys,json; "
            "d=json.load(sys.stdin); "
            "assert d['hook_event_name'] == 'post_tool'; "
            "assert d['tool_status'] == 'success'; "
            "assert d['tool_output'] == {'r': 1}; "
            "assert d['tool_name'] == 'bash'; "
            "assert d['tool_call_id'] == 'tc1'; "
            "sys.stdout.write(json.dumps("
            "{'decision':'deny','reason':'asserts passed'}"
            "))"
            '"'
        )
        handler = HooksManager([
            _make_tool_hook("inspect", script, type=HookType.POST_TOOL)
        ])
        final_text, _events = await _drain_post_tool_chain(
            handler,
            ctx,
            tool_name="bash",
            tool_call_id="tc1",
            tool_input={"command": "ls"},
            tool_status="success",
            tool_output={"r": 1},
            tool_error=None,
            duration_ms=10.0,
            initial_text="ignored",
        )
        assert final_text == "asserts passed"

    @pytest.mark.asyncio
    async def test_strict_failure_empties_text(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([
            _make_tool_hook("guard", "exit 1", type=HookType.POST_TOOL, strict=True),
            _make_tool_hook("second", _deny_cmd("replaced"), type=HookType.POST_TOOL),
        ])
        final_text, events = await _drain_post_tool_chain(
            handler,
            ctx,
            tool_name="bash",
            tool_call_id="tc1",
            tool_input={},
            tool_status="success",
            tool_output={"r": 1},
            tool_error=None,
            duration_ms=10.0,
            initial_text="sensitive data",
        )
        assert final_text == ""
        errors = [
            e
            for e in events
            if isinstance(e, HookEndEvent) and e.status == HookMessageSeverity.ERROR
        ]
        assert any("strict" in (e.content or "") for e in errors)
        # Second hook must not have started
        starts = [e for e in events if isinstance(e, HookStartEvent)]
        assert [e.hook_name for e in starts] == ["guard"]

    @pytest.mark.asyncio
    async def test_strict_timeout_empties_text(self, ctx: HookSessionContext) -> None:
        handler = HooksManager([
            _make_tool_hook(
                "slow", "sleep 10", type=HookType.POST_TOOL, timeout=0.1, strict=True
            )
        ])
        final_text, _events = await _drain_post_tool_chain(
            handler,
            ctx,
            tool_name="bash",
            tool_call_id="tc1",
            tool_input={},
            tool_status="success",
            tool_output={"r": 1},
            tool_error=None,
            duration_ms=10.0,
            initial_text="sensitive data",
        )
        assert final_text == ""


class TestStrictValidation:
    def test_strict_forbidden_on_post_agent(self) -> None:
        with pytest.raises(ValueError, match="strict is only valid for tool hooks"):
            HookConfig(
                name="bad", type=HookType.POST_AGENT, command="echo ok", strict=True
            )

    def test_strict_allowed_on_pre_tool(self) -> None:
        hook = HookConfig(
            name="guard", type=HookType.PRE_TOOL, command="echo ok", strict=True
        )
        assert hook.strict is True

    def test_strict_allowed_on_post_tool(self) -> None:
        hook = HookConfig(
            name="redact", type=HookType.POST_TOOL, command="echo ok", strict=True
        )
        assert hook.strict is True


def _stub_tool_call(call_id: str = "call_1", arguments: str = "{}") -> ToolCall:
    return ToolCall(
        id=call_id,
        index=0,
        function=FunctionCall(name="stub_tool", arguments=arguments),
    )


class TestStructuredResponseParsing:
    def test_empty_stdout_returns_none(self) -> None:
        # Empty stdout is the only legitimate "passthrough" signal — the
        # hook explicitly chose to do nothing.
        assert _parse_structured_response("") is None

    def test_non_json_stdout_raises(self) -> None:
        # Any non-empty stdout MUST be a structured response. Free-form
        # text (debug logs, accidental prints) is a contract violation;
        # diagnostics belong on stderr.
        with pytest.raises(HookOutputError, match="not valid JSON"):
            _parse_structured_response("hello world")

    def test_truncated_json_raises(self) -> None:
        with pytest.raises(HookOutputError, match="not valid JSON"):
            _parse_structured_response('{"decision": "deny", "reason": "no"')

    def test_json_array_raises(self) -> None:
        with pytest.raises(HookOutputError, match="expected an object"):
            _parse_structured_response("[1, 2, 3]")

    def test_json_scalar_raises(self) -> None:
        with pytest.raises(HookOutputError, match="expected an object"):
            _parse_structured_response('"just a string"')

    def test_schema_mismatch_raises(self) -> None:
        # "maybe" is not a valid Literal value for `decision`.
        with pytest.raises(HookOutputError, match="schema"):
            _parse_structured_response('{"decision": "maybe"}')

    def test_empty_object_parses_to_passthrough(self) -> None:
        # {} is valid: no rewrite, no system_message, just an explicit OK.
        result = _parse_structured_response("{}")
        assert result is not None
        assert result.hook_specific_output.tool_input is None
        assert result.system_message is None

    def test_unknown_fields_ignored(self) -> None:
        # Forward-compat: reserved fields we may grow into are tolerated.
        result = _parse_structured_response(
            '{"decision": "allow", "continue": false, "future_field": 42}'
        )
        assert result is not None
        assert result.hook_specific_output.tool_input is None

    def test_unknown_nested_fields_ignored(self) -> None:
        result = _parse_structured_response(
            '{"hook_specific_output": {"future_subfield": "x"}}'
        )
        assert result is not None
        assert result.hook_specific_output.tool_input is None

    def test_tool_input_parses(self) -> None:
        result = _parse_structured_response(
            '{"hook_specific_output": {"tool_input": {"command": "ls -la"}}}'
        )
        assert result is not None
        assert result.hook_specific_output.tool_input == {"command": "ls -la"}

    def test_top_level_tool_input_ignored(self) -> None:
        # Backwards-incompatible safeguard: a flat tool_input at the top
        # level (the v1 shape before nesting) is silently ignored.
        result = _parse_structured_response('{"tool_input": {"command": "x"}}')
        assert result is not None
        assert result.hook_specific_output.tool_input is None

    def test_system_message_parses(self) -> None:
        result = _parse_structured_response('{"system_message": "audited"}')
        assert result is not None
        assert result.system_message == "audited"

    def test_default_construct_defaults(self) -> None:
        # The model's defaults are stable so manager logic can rely on them.
        m = HookStructuredResponse()
        assert m.system_message is None
        assert m.hook_specific_output.tool_input is None


class TestPreToolRewrite:
    @pytest.mark.asyncio
    async def test_single_hook_rewrites_tool_input(
        self, ctx: HookSessionContext
    ) -> None:
        script = (
            f'{sys.executable} -c "'
            "import json,sys; "
            "json.dump({'hook_specific_output': "
            "{'tool_input': {'command': 'echo rewritten'}}}, sys.stdout)"
            '"'
        )
        handler = HooksManager([
            _make_tool_hook("rewriter", script, type=HookType.PRE_TOOL, match="bash")
        ])
        events = [
            ev
            async for ev in _run(
                handler,
                HookType.PRE_TOOL,
                ctx,
                tool_name="bash",
                tool_call_id="tc1",
                tool_input={"command": "echo original"},
            )
        ]
        rewrites = [e for e in events if isinstance(e, HookToolInputRewrite)]
        assert len(rewrites) == 1
        assert rewrites[0].hook_name == "rewriter"
        assert rewrites[0].tool_input == {"command": "echo rewritten"}
        # No denial, no after-tool replacement event
        assert not any(isinstance(e, HookToolDenial) for e in events)

    @pytest.mark.asyncio
    async def test_rewrite_pipeline_composes_left_to_right(
        self, ctx: HookSessionContext
    ) -> None:
        # First hook prepends "echo "; second hook reads its piped input and
        # uppercases the command. The second hook must see the FIRST hook's
        # rewrite, proving manager threads tool_input through the chain.
        first = (
            f'{sys.executable} -c "'
            "import json,sys; "
            "d=json.load(sys.stdin); "
            "cmd=d['tool_input'].get('command',''); "
            "json.dump({'hook_specific_output': "
            "{'tool_input': {**d['tool_input'], 'command': 'echo '+cmd}}}, sys.stdout)"
            '"'
        )
        second = (
            f'{sys.executable} -c "'
            "import json,sys; "
            "d=json.load(sys.stdin); "
            "cmd=d['tool_input'].get('command',''); "
            "json.dump({'hook_specific_output': "
            "{'tool_input': {**d['tool_input'], 'command': cmd.upper()}}}, sys.stdout)"
            '"'
        )
        handler = HooksManager([
            _make_tool_hook("first", first, type=HookType.PRE_TOOL),
            _make_tool_hook("second", second, type=HookType.PRE_TOOL),
        ])
        events = [
            ev
            async for ev in _run(
                handler,
                HookType.PRE_TOOL,
                ctx,
                tool_name="bash",
                tool_call_id="tc1",
                tool_input={"command": "hi"},
            )
        ]
        # The manager emits one HookToolInputRewrite per rewriting hook
        # (in chronological order), each carrying the cumulative
        # ``tool_input`` at that step. The agent loop validates each as
        # it arrives and aborts the chain on the first invalid one.
        rewrites = [e for e in events if isinstance(e, HookToolInputRewrite)]
        assert [r.hook_name for r in rewrites] == ["first", "second"]
        assert rewrites[0].tool_input == {"command": "echo hi"}
        assert rewrites[1].tool_input == {"command": "ECHO HI"}

    @pytest.mark.asyncio
    async def test_rewrite_chain_streams_per_hook(
        self, ctx: HookSessionContext
    ) -> None:
        # Three hooks each rewriting; expect exactly three
        # HookToolInputRewrite events in the stream, one per hook, each
        # attributed to its source.
        def script(out: str) -> str:
            return (
                f'{sys.executable} -c "'
                "import json,sys; "
                "json.dump({'hook_specific_output': "
                f"{{'tool_input': {{'command': {out!r}}}}}}}, sys.stdout)"
                '"'
            )

        handler = HooksManager([
            _make_tool_hook("a", script("a"), type=HookType.PRE_TOOL),
            _make_tool_hook("b", script("b"), type=HookType.PRE_TOOL),
            _make_tool_hook("c", script("c"), type=HookType.PRE_TOOL),
        ])
        events = [
            ev
            async for ev in _run(
                handler,
                HookType.PRE_TOOL,
                ctx,
                tool_name="bash",
                tool_call_id="tc1",
                tool_input={"command": "orig"},
            )
        ]
        rewrites = [e for e in events if isinstance(e, HookToolInputRewrite)]
        assert [r.hook_name for r in rewrites] == ["a", "b", "c"]
        assert [r.tool_input["command"] for r in rewrites] == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_structured_system_message_shown_on_passthrough(
        self, ctx: HookSessionContext
    ) -> None:
        script = (
            f'{sys.executable} -c "'
            "import json,sys; "
            "json.dump({'system_message': 'logged'}, sys.stdout)"
            '"'
        )
        handler = HooksManager([
            _make_tool_hook("audit", script, type=HookType.PRE_TOOL)
        ])
        events = [
            ev
            async for ev in _run(
                handler,
                HookType.PRE_TOOL,
                ctx,
                tool_name="bash",
                tool_call_id="tc1",
                tool_input={"command": "ls"},
            )
        ]
        ends = [e for e in events if isinstance(e, HookEndEvent)]
        assert len(ends) == 1
        assert ends[0].content == "logged"
        # No rewrite, no denial
        assert not any(isinstance(e, HookToolInputRewrite) for e in events)
        assert not any(isinstance(e, HookToolDenial) for e in events)

    @pytest.mark.asyncio
    async def test_non_json_stdout_is_a_warning(self, ctx: HookSessionContext) -> None:
        # The contract is strict: stdout is for the JSON response, full
        # stop. A hook that prints free-form text on stdout (e.g. debug
        # logs that should have gone to stderr) is treated as a failure
        # — surfaced as a UI warning, no denial, no rewrite.
        handler = HooksManager([
            _make_tool_hook(
                "chatty", "echo 'just some debug output'", type=HookType.PRE_TOOL
            )
        ])
        events = [
            ev
            async for ev in _run(
                handler,
                HookType.PRE_TOOL,
                ctx,
                tool_name="bash",
                tool_call_id="tc1",
                tool_input={"command": "ls"},
            )
        ]
        assert not any(isinstance(e, HookToolDenial) for e in events)
        assert not any(isinstance(e, HookToolInputRewrite) for e in events)
        warnings = [
            e
            for e in events
            if isinstance(e, HookEndEvent) and e.status == HookMessageSeverity.WARNING
        ]
        assert len(warnings) == 1
        assert warnings[0].content and "not valid JSON" in warnings[0].content

    @pytest.mark.asyncio
    async def test_strict_mode_escalates_invalid_stdout_to_denial(
        self, ctx: HookSessionContext
    ) -> None:
        # With strict=true the "bad stdout" path is escalated through
        # HookHandler.on_strict_failure — exactly like a non-zero exit
        # would be. For pre_tool that means denying the call with the
        # parse error as the reason.
        handler = HooksManager([
            _make_tool_hook(
                "guard", "echo 'not actually json'", type=HookType.PRE_TOOL, strict=True
            )
        ])
        events = [
            ev
            async for ev in _run(
                handler,
                HookType.PRE_TOOL,
                ctx,
                tool_name="bash",
                tool_call_id="tc1",
                tool_input={"command": "ls"},
            )
        ]
        denials = [e for e in events if isinstance(e, HookToolDenial)]
        assert len(denials) == 1
        assert "invalid response" in denials[0].content


class TestAgentLoopIntegration:
    @pytest.mark.asyncio
    async def test_post_agent_hook_runs_after_turn(self) -> None:
        backend = FakeBackend(mock_llm_chunk(content="Hello!"))
        hooks = [_make_hook(name="post-lint", command="echo ok")]
        agent_loop = build_test_agent_loop(
            backend=backend, hook_config_result=HookConfigResult(hooks=hooks, issues=[])
        )

        events = [ev async for ev in agent_loop.act("hi")]
        event_types = [type(e).__name__ for e in events]
        assert "HookStartEvent" in event_types
        assert "HookEndEvent" in event_types

    @pytest.mark.asyncio
    async def test_post_agent_hook_retry_reinjects_message(self) -> None:
        backend = FakeBackend([
            [mock_llm_chunk(content="first response")],
            [mock_llm_chunk(content="second response")],
        ])

        counter_file = Path.cwd() / ".hook_counter"
        # On the first call, emit a JSON deny so the manager treats it as a
        # retry-with-reason; on the second call, emit nothing so the agent
        # loop terminates normally.
        script = (
            f'{sys.executable} -c "'
            f"from pathlib import Path; "
            f"import sys, json; "
            f"p = Path({str(counter_file)!r}); "
            f"c = int(p.read_text()) if p.exists() else 0; "
            f"p.write_text(str(c + 1)); "
            f"sys.stdout.write(json.dumps({{'decision':'deny','reason':'fix this'}}) if c == 0 else '')"
            f'"'
        )
        hooks = [_make_hook(name="retry-hook", command=script)]
        agent_loop = build_test_agent_loop(
            backend=backend, hook_config_result=HookConfigResult(hooks=hooks, issues=[])
        )

        events = [ev async for ev in agent_loop.act("hi")]
        assistant_events = [e for e in events if isinstance(e, AssistantEvent)]
        assert len(assistant_events) == 2

        user_messages = [
            m for m in agent_loop.messages if m.role.value == "user" and m.injected
        ]
        assert any("fix this" in (m.content or "") for m in user_messages)

    @pytest.mark.asyncio
    async def test_no_hooks_no_events(self) -> None:
        backend = FakeBackend(mock_llm_chunk(content="Hello!"))
        agent_loop = build_test_agent_loop(backend=backend)

        events = [ev async for ev in agent_loop.act("hi")]
        hook_events = [
            e for e in events if isinstance(e, (HookStartEvent, HookEndEvent))
        ]
        assert hook_events == []

    @pytest.mark.asyncio
    async def test_pre_tool_deny_prevents_invocation(self) -> None:
        tool_call = _stub_tool_call("call_block")
        config = build_test_vibe_config(enabled_tools=["stub_tool"])
        hooks = [
            _make_tool_hook(
                "deny-stub",
                _deny_cmd("denied by policy"),
                type=HookType.PRE_TOOL,
                match="stub_tool",
            )
        ]
        backend = FakeBackend([
            [mock_llm_chunk(content="Calling stub.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="ok then")],
        ])
        agent_loop = build_test_agent_loop(
            config=config,
            agent_name=BuiltinAgentName.AUTO_APPROVE,
            backend=backend,
            hook_config_result=HookConfigResult(hooks=hooks, issues=[]),
        )
        agent_loop.tool_manager._all_tools["stub_tool"] = FakeTool

        events = [ev async for ev in agent_loop.act("run it")]
        tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
        assert len(tool_results) == 1
        assert tool_results[0].skipped is True
        assert tool_results[0].skip_reason is not None
        assert "denied by policy" in tool_results[0].skip_reason
        assert agent_loop.stats.tool_calls_hook_denied == 1
        assert agent_loop.stats.tool_calls_rejected == 0

    @pytest.mark.asyncio
    async def test_pre_tool_deny_payload_appears_in_messages(self) -> None:
        tool_call = _stub_tool_call("call_msg")
        config = build_test_vibe_config(enabled_tools=["stub_tool"])
        hooks = [
            _make_tool_hook(
                "deny", _deny_cmd("forbidden"), type=HookType.PRE_TOOL, match="*"
            )
        ]
        backend = FakeBackend([
            [mock_llm_chunk(content="Try.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="acknowledged")],
        ])
        agent_loop = build_test_agent_loop(
            config=config,
            agent_name=BuiltinAgentName.AUTO_APPROVE,
            backend=backend,
            hook_config_result=HookConfigResult(hooks=hooks, issues=[]),
        )
        agent_loop.tool_manager._all_tools["stub_tool"] = FakeTool

        async for _ev in agent_loop.act("go"):
            pass

        tool_msgs = [m for m in agent_loop.messages if m.role.value == "tool"]
        assert any("forbidden" in (m.content or "") for m in tool_msgs)

    @pytest.mark.asyncio
    async def test_post_tool_replaces_llm_text_not_event(self) -> None:
        tool_call = _stub_tool_call("call_after")
        config = build_test_vibe_config(enabled_tools=["stub_tool"])
        hooks = [
            _make_tool_hook(
                "rewrite",
                _deny_cmd("REWRITTEN"),
                type=HookType.POST_TOOL,
                match="stub_tool",
            )
        ]
        backend = FakeBackend([
            [mock_llm_chunk(content="Calling.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="done")],
        ])
        agent_loop = build_test_agent_loop(
            config=config,
            agent_name=BuiltinAgentName.AUTO_APPROVE,
            backend=backend,
            hook_config_result=HookConfigResult(hooks=hooks, issues=[]),
        )
        agent_loop.tool_manager._all_tools["stub_tool"] = FakeTool

        events = [ev async for ev in agent_loop.act("go")]

        tool_results = [
            e for e in events if isinstance(e, ToolResultEvent) and not e.skipped
        ]
        assert len(tool_results) == 1
        # UI event preserves the original result_model
        assert tool_results[0].result is not None

        # But the LLM-bound message has been replaced.
        tool_msgs = [m for m in agent_loop.messages if m.role.value == "tool"]
        assert any((m.content or "").strip() == "REWRITTEN" for m in tool_msgs)

    @pytest.mark.asyncio
    async def test_post_tool_matcher_skips_non_matching(self) -> None:
        tool_call = _stub_tool_call("call_nope")
        config = build_test_vibe_config(enabled_tools=["stub_tool"])
        hooks = [
            _make_tool_hook(
                "wrong-match",
                _deny_cmd("should not run"),
                type=HookType.POST_TOOL,
                match="bash",
            )
        ]
        backend = FakeBackend([
            [mock_llm_chunk(content="Calling.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="done")],
        ])
        agent_loop = build_test_agent_loop(
            config=config,
            agent_name=BuiltinAgentName.AUTO_APPROVE,
            backend=backend,
            hook_config_result=HookConfigResult(hooks=hooks, issues=[]),
        )
        agent_loop.tool_manager._all_tools["stub_tool"] = FakeTool

        async for _ev in agent_loop.act("go"):
            pass

        tool_msgs = [m for m in agent_loop.messages if m.role.value == "tool"]
        # The hook's stdout must not appear in the tool message — the matcher
        # skipped it.
        assert not any("should not run" in (m.content or "") for m in tool_msgs)

    @pytest.mark.asyncio
    async def test_pre_tool_rewrite_applies_to_tool_invocation(self) -> None:
        # The hook rewrites tool_input so the tool runs with text="rewritten".
        # The result message echoes that value (FakeTool returns it as
        # `message`), proving the rewrite reached the tool.
        tool_call = _stub_tool_call("call_rw", arguments='{"text": "original"}')
        config = build_test_vibe_config(enabled_tools=["stub_tool"])
        script = (
            f'{sys.executable} -c "'
            "import json,sys; "
            "json.dump({'hook_specific_output': "
            "{'tool_input': {'text': 'rewritten'}}}, sys.stdout)"
            '"'
        )
        hooks = [
            _make_tool_hook(
                "rewriter", script, type=HookType.PRE_TOOL, match="stub_tool"
            )
        ]
        backend = FakeBackend([
            [mock_llm_chunk(content="Calling.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="done")],
        ])
        agent_loop = build_test_agent_loop(
            config=config,
            agent_name=BuiltinAgentName.AUTO_APPROVE,
            backend=backend,
            hook_config_result=HookConfigResult(hooks=hooks, issues=[]),
        )
        agent_loop.tool_manager._all_tools["stub_tool"] = FakeTool

        async for _ev in agent_loop.act("go"):
            pass

        tool_msgs = [m for m in agent_loop.messages if m.role.value == "tool"]
        assert any("message: rewritten" in (m.content or "") for m in tool_msgs)
        # And the assistant message's tool_call arguments were patched so
        # subsequent LLM turns see what actually ran.
        assistant_with_calls = [
            m
            for m in agent_loop.messages
            if m.role.value == "assistant" and m.tool_calls
        ]
        assert assistant_with_calls
        last_tool_calls = assistant_with_calls[-1].tool_calls
        assert last_tool_calls is not None
        tc_args = last_tool_calls[0].function.arguments
        assert tc_args is not None
        assert '"text": "rewritten"' in tc_args

    @pytest.mark.asyncio
    async def test_pre_tool_rewrite_is_persisted_to_messages_jsonl(self) -> None:
        # The in-memory patch (covered above) is necessary but not
        # sufficient: the on-disk ``messages.jsonl`` must also reflect the
        # rewritten args, otherwise a resumed session would replay the
        # model's original (never-actually-ran) intent to the LLM.
        tool_call = _stub_tool_call("call_persist", arguments='{"text": "original"}')
        config = build_test_vibe_config(
            enabled_tools=["stub_tool"],
            session_logging=SessionLoggingConfig(enabled=True),
        )
        script = (
            f'{sys.executable} -c "'
            "import json,sys; "
            "json.dump({'hook_specific_output': "
            "{'tool_input': {'text': 'rewritten'}}}, sys.stdout)"
            '"'
        )
        hooks = [
            _make_tool_hook(
                "rewriter", script, type=HookType.PRE_TOOL, match="stub_tool"
            )
        ]
        backend = FakeBackend([
            [mock_llm_chunk(content="Calling.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="done")],
        ])
        agent_loop = build_test_agent_loop(
            config=config,
            agent_name=BuiltinAgentName.AUTO_APPROVE,
            backend=backend,
            hook_config_result=HookConfigResult(hooks=hooks, issues=[]),
        )
        agent_loop.tool_manager._all_tools["stub_tool"] = FakeTool

        async for _ev in agent_loop.act("go"):
            pass

        jsonl_path = agent_loop.session_logger.messages_filepath
        lines = [
            json.loads(line)
            for line in jsonl_path.read_text().splitlines()
            if line.strip()
        ]
        assistants_with_calls = [
            m for m in lines if m.get("role") == "assistant" and m.get("tool_calls")
        ]
        assert assistants_with_calls, f"no assistant tool call in {jsonl_path}"
        persisted_args = assistants_with_calls[-1]["tool_calls"][0]["function"][
            "arguments"
        ]
        assert '"text": "rewritten"' in persisted_args, (
            f"messages.jsonl still contains the original args: {persisted_args}"
        )

    @pytest.mark.asyncio
    async def test_pre_tool_rewrite_validation_failure_denies(self) -> None:
        # The hook returns a tool_input with a wrong type for `text` (int
        # instead of str). Re-validation should fail and the rewrite is
        # converted to a denial that the LLM sees as a tool error.
        tool_call = _stub_tool_call("call_bad")
        config = build_test_vibe_config(enabled_tools=["stub_tool"])
        # FakeToolArgs.text is `str`. A list forces a hard type mismatch
        # that pydantic cannot coerce, so we get a real ValidationError.
        script = (
            f'{sys.executable} -c "'
            "import json,sys; "
            "json.dump({'hook_specific_output': "
            "{'tool_input': {'text': [1,2,3]}}}, sys.stdout)"
            '"'
        )
        hooks = [
            _make_tool_hook(
                "bad-rewriter", script, type=HookType.PRE_TOOL, match="stub_tool"
            )
        ]
        backend = FakeBackend([
            [mock_llm_chunk(content="Calling.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="acknowledged")],
        ])
        agent_loop = build_test_agent_loop(
            config=config,
            agent_name=BuiltinAgentName.AUTO_APPROVE,
            backend=backend,
            hook_config_result=HookConfigResult(hooks=hooks, issues=[]),
        )
        agent_loop.tool_manager._all_tools["stub_tool"] = FakeTool

        events = [ev async for ev in agent_loop.act("go")]
        tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
        assert len(tool_results) == 1
        assert tool_results[0].skipped is True
        assert tool_results[0].skip_reason is not None
        assert "failed validation" in tool_results[0].skip_reason
        assert "bad-rewriter" in tool_results[0].skip_reason
        assert agent_loop.stats.tool_calls_hook_denied == 1

    @pytest.mark.asyncio
    async def test_invalid_intermediate_rewrite_stops_subsequent_hooks(self) -> None:
        # Hook 1 produces an invalid tool_input (text=[1,2,3] but the
        # schema expects str). Hook 2 would have produced a valid rewrite
        # but must NEVER run: the agent loop validates after each hook
        # and aborts the chain at the first failure.
        tool_call = _stub_tool_call("call_abort")
        config = build_test_vibe_config(enabled_tools=["stub_tool"])
        bad_script = (
            f'{sys.executable} -c "'
            "import json,sys; "
            "json.dump({'hook_specific_output': "
            "{'tool_input': {'text': [1,2,3]}}}, sys.stdout)"
            '"'
        )
        sentinel = Path.cwd() / ".second_hook_ran"
        # If the second hook ever runs it would touch this file, which we
        # then assert was NOT created.
        good_script = (
            f'{sys.executable} -c "'
            "from pathlib import Path; "
            f"Path({str(sentinel)!r}).write_text('ran'); "
            "import sys,json; "
            "json.dump({'hook_specific_output': "
            "{'tool_input': {'text': 'salvaged'}}}, sys.stdout)"
            '"'
        )
        hooks = [
            _make_tool_hook(
                "broken", bad_script, type=HookType.PRE_TOOL, match="stub_tool"
            ),
            _make_tool_hook(
                "would-fix", good_script, type=HookType.PRE_TOOL, match="stub_tool"
            ),
        ]
        backend = FakeBackend([
            [mock_llm_chunk(content="Calling.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="acknowledged")],
        ])
        agent_loop = build_test_agent_loop(
            config=config,
            agent_name=BuiltinAgentName.AUTO_APPROVE,
            backend=backend,
            hook_config_result=HookConfigResult(hooks=hooks, issues=[]),
        )
        agent_loop.tool_manager._all_tools["stub_tool"] = FakeTool

        events = [ev async for ev in agent_loop.act("go")]
        tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
        assert len(tool_results) == 1
        assert tool_results[0].skipped is True
        assert tool_results[0].skip_reason is not None
        assert "broken" in tool_results[0].skip_reason
        assert not sentinel.exists(), (
            "second hook should NOT have run after the first hook's invalid rewrite"
        )

    @pytest.mark.asyncio
    async def test_serialize_tool_input_failure_rejects_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tool_call = _stub_tool_call("call_set")
        config = build_test_vibe_config(enabled_tools=["stub_tool"])
        backend = FakeBackend([
            [mock_llm_chunk(content="Calling.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="ok")],
        ])
        agent_loop = build_test_agent_loop(
            config=config, agent_name=BuiltinAgentName.AUTO_APPROVE, backend=backend
        )
        agent_loop.tool_manager._all_tools["stub_tool"] = FakeTool

        original = FakeToolArgs.model_dump

        def _blow_up(self: Any, **kwargs: Any) -> Any:
            if kwargs.get("mode") == "json":
                raise TypeError("cannot serialize")
            return original(self, **kwargs)

        monkeypatch.setattr(FakeToolArgs, "model_dump", _blow_up)

        events = [ev async for ev in agent_loop.act("go")]
        tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
        assert len(tool_results) == 1
        assert tool_results[0].error is not None
        assert "serialize" in tool_results[0].error.lower()


class TestHookOutputCap:
    @pytest.mark.asyncio
    async def test_stdout_capped_at_limit(
        self, sample_invocation: PostAgentInvocation
    ) -> None:
        from vibe.core.hooks.executor import _MAX_OUTPUT_BYTES

        overflow = _MAX_OUTPUT_BYTES + 4096
        script = (
            f'{sys.executable} -c "'
            f"import sys; sys.stdout.buffer.write(b'A' * {overflow})"
            '"'
        )
        hook = _make_hook(command=script)
        result = await HookExecutor().run(hook, sample_invocation)
        assert result.exit_code == 0
        assert len(result.stdout) <= _MAX_OUTPUT_BYTES

    @pytest.mark.asyncio
    async def test_stderr_capped_at_limit(
        self, sample_invocation: PostAgentInvocation
    ) -> None:
        from vibe.core.hooks.executor import _MAX_OUTPUT_BYTES

        overflow = _MAX_OUTPUT_BYTES + 4096
        script = (
            f'{sys.executable} -c "'
            f"import sys; sys.stderr.buffer.write(b'E' * {overflow})"
            '"'
        )
        hook = _make_hook(command=script)
        result = await HookExecutor().run(hook, sample_invocation)
        assert result.exit_code == 0
        assert len(result.stderr) <= _MAX_OUTPUT_BYTES
