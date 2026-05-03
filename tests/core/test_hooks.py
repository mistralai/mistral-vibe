from __future__ import annotations

from pathlib import Path
import sys

import pytest
import tomli_w

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.config import VibeConfig
from vibe.core.hooks.config import (
    HookConfig,
    HookConfigResult,
    _load_hooks_file,
    load_hooks_from_fs,
)
from vibe.core.hooks.executor import HookExecutor
from vibe.core.hooks.manager import HooksManager
from vibe.core.hooks.models import (
    HookEndEvent,
    HookInvocation,
    HookMessageSeverity,
    HookStartEvent,
    HookType,
    HookUserMessage,
)
from vibe.core.types import BaseEvent


@pytest.fixture
def sample_invocation() -> HookInvocation:
    return HookInvocation(
        session_id="test-session",
        transcript_path="",
        cwd=str(Path.cwd()),
        hook_event_name="post_agent_turn",
    )


@pytest.fixture
def config_hooks_disabled() -> VibeConfig:
    return VibeConfig(enable_experimental_hooks=False)


@pytest.fixture
def config_hooks_enabled() -> VibeConfig:
    return VibeConfig(enable_experimental_hooks=True)


def _write_hooks_toml(path: Path, hooks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        tomli_w.dump({"hooks": hooks}, f)


def _make_hook(
    name: str = "test-hook", command: str = "echo ok", timeout: float = 30.0
) -> HookConfig:
    return HookConfig(
        name=name, type=HookType.POST_AGENT_TURN, command=command, timeout=timeout
    )


class TestConfigLoading:
    def test_load_from_global_file(
        self, config_dir: Path, config_hooks_enabled: VibeConfig
    ) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [
                {
                    "name": "lint",
                    "type": HookType.POST_AGENT_TURN,
                    "command": "echo lint",
                }
            ],
        )
        result = load_hooks_from_fs(config_hooks_enabled)
        assert len(result.hooks) == 1
        assert result.hooks[0].name == "lint"
        assert result.issues == []

    def test_load_from_both_global_and_project(
        self,
        config_dir: Path,
        tmp_working_directory: Path,
        config_hooks_enabled: VibeConfig,
    ) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [
                {
                    "name": "global-hook",
                    "type": "post_agent_turn",
                    "command": "echo global",
                }
            ],
        )
        project_vibe = tmp_working_directory / ".vibe"
        _write_hooks_toml(
            project_vibe / "hooks.toml",
            [
                {
                    "name": "project-hook",
                    "type": "post_agent_turn",
                    "command": "echo project",
                }
            ],
        )
        from vibe.core.trusted_folders import trusted_folders_manager

        trusted_folders_manager.add_trusted(tmp_working_directory)

        result = load_hooks_from_fs(config_hooks_enabled)
        assert len(result.hooks) == 2
        names = {h.name for h in result.hooks}
        assert names == {"global-hook", "project-hook"}

    def test_project_file_skipped_when_untrusted(
        self, tmp_working_directory: Path, config_hooks_enabled: VibeConfig
    ) -> None:
        project_vibe = tmp_working_directory / ".vibe"
        _write_hooks_toml(
            project_vibe / "hooks.toml",
            [
                {
                    "name": "sneaky-hook",
                    "type": "post_agent_turn",
                    "command": "echo sneaky",
                }
            ],
        )
        result = load_hooks_from_fs(config_hooks_enabled)
        assert not any(h.name == "sneaky-hook" for h in result.hooks)

    def test_duplicate_hook_name_detection(
        self,
        config_dir: Path,
        tmp_working_directory: Path,
        config_hooks_enabled: VibeConfig,
    ) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "dup-hook", "type": "post_agent_turn", "command": "echo global"}],
        )
        project_vibe = tmp_working_directory / ".vibe"
        _write_hooks_toml(
            project_vibe / "hooks.toml",
            [
                {
                    "name": "dup-hook",
                    "type": "post_agent_turn",
                    "command": "echo project",
                }
            ],
        )
        from vibe.core.trusted_folders import trusted_folders_manager

        trusted_folders_manager.add_trusted(tmp_working_directory)

        result = load_hooks_from_fs(config_hooks_enabled)
        assert len(result.hooks) == 1
        assert any("Duplicate" in i.message for i in result.issues)

    def test_toml_parse_error_reported(
        self, config_dir: Path, config_hooks_enabled: VibeConfig
    ) -> None:
        hooks_file = config_dir / "hooks.toml"
        hooks_file.write_text("this is not valid toml [[[", encoding="utf-8")
        result = load_hooks_from_fs(config_hooks_enabled)
        assert result.hooks == []
        assert len(result.issues) == 1
        assert (
            "parse" in result.issues[0].message.lower()
            or "Failed" in result.issues[0].message
        )

    def test_validation_error_reported(
        self, config_dir: Path, config_hooks_enabled: VibeConfig
    ) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "bad", "type": "InvalidType", "command": "echo"}],
        )
        result = load_hooks_from_fs(config_hooks_enabled)
        assert result.hooks == []
        assert len(result.issues) == 1

    def test_missing_command_reported(
        self, config_dir: Path, config_hooks_enabled: VibeConfig
    ) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml", [{"name": "no-cmd", "type": "post_agent_turn"}]
        )
        result = load_hooks_from_fs(config_hooks_enabled)
        assert result.hooks == []
        assert len(result.issues) == 1

    def test_empty_command_reported(
        self, config_dir: Path, config_hooks_enabled: VibeConfig
    ) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "empty-cmd", "type": "post_agent_turn", "command": "   "}],
        )
        result = load_hooks_from_fs(config_hooks_enabled)
        assert result.hooks == []
        assert len(result.issues) == 1

    def test_default_timeout(
        self, config_dir: Path, config_hooks_enabled: VibeConfig
    ) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [{"name": "h", "type": "post_agent_turn", "command": "echo ok"}],
        )
        result = load_hooks_from_fs(config_hooks_enabled)
        assert result.hooks[0].timeout == 30.0

    def test_nonexistent_file_returns_empty(self, tmp_path: Path) -> None:
        result = _load_hooks_file(tmp_path / "missing.toml")
        assert result.hooks == []
        assert result.issues == []

    def test_hooks_disabled_returns_empty(
        self, config_dir: Path, config_hooks_disabled: VibeConfig
    ) -> None:
        _write_hooks_toml(
            config_dir / "hooks.toml",
            [
                {
                    "name": "lint",
                    "type": HookType.POST_AGENT_TURN,
                    "command": "echo lint",
                }
            ],
        )
        result = load_hooks_from_fs(config_hooks_disabled)
        assert result.hooks == []
        assert result.issues == []


class TestHookExecutor:
    @pytest.mark.asyncio
    async def test_exit_0_success(self, sample_invocation: HookInvocation) -> None:
        hook = _make_hook(command="echo success")
        result = await HookExecutor().run(hook, sample_invocation)
        assert result.exit_code == 0
        assert result.stdout == "success"
        assert not result.timed_out

    @pytest.mark.asyncio
    async def test_exit_2_retry(self, sample_invocation: HookInvocation) -> None:
        hook = _make_hook(command="echo 'fix this'; exit 2")
        result = await HookExecutor().run(hook, sample_invocation)
        assert result.exit_code == 2
        assert "fix this" in result.stdout
        assert not result.timed_out

    @pytest.mark.asyncio
    async def test_other_exit_code(self, sample_invocation: HookInvocation) -> None:
        hook = _make_hook(command="echo 'oops'; exit 1")
        result = await HookExecutor().run(hook, sample_invocation)
        assert result.exit_code == 1
        assert "oops" in result.stdout

    @pytest.mark.asyncio
    async def test_timeout(self, sample_invocation: HookInvocation) -> None:
        hook = _make_hook(command="sleep 60", timeout=0.5)
        result = await HookExecutor().run(hook, sample_invocation)
        assert result.timed_out
        assert result.exit_code is None

    @pytest.mark.asyncio
    async def test_stderr_captured_separately(
        self, sample_invocation: HookInvocation
    ) -> None:
        hook = _make_hook(command="echo out; echo err >&2")
        result = await HookExecutor().run(hook, sample_invocation)
        assert result.exit_code == 0
        assert result.stdout == "out"
        assert result.stderr == "err"

    @pytest.mark.asyncio
    async def test_stdin_json_received(self, sample_invocation: HookInvocation) -> None:
        hook = _make_hook(
            command=f"{sys.executable} -c \"import sys,json; d=json.load(sys.stdin); print(d['session_id'])\""
        )
        result = await HookExecutor().run(hook, sample_invocation)
        assert result.exit_code == 0
        assert result.stdout == "test-session"


class TestHooksManager:
    @pytest.mark.asyncio
    async def test_exit_0_emits_start_and_end(self) -> None:
        handler = HooksManager([_make_hook(command="echo ok")])
        from vibe.core.config import SessionLoggingConfig
        from vibe.core.session.session_logger import SessionLogger

        logger = SessionLogger(SessionLoggingConfig(enabled=False), "test-id")
        events: list[BaseEvent | HookUserMessage] = []
        async for ev in handler.run(HookType.POST_AGENT_TURN, "sess", logger):
            events.append(ev)

        event_types = [type(e).__name__ for e in events]
        assert "HookStartEvent" in event_types
        assert "HookEndEvent" in event_types
        # Exit 0 with no retry = no HookUserMessage
        assert not any(isinstance(e, HookUserMessage) for e in events)

    @pytest.mark.asyncio
    async def test_exit_2_emits_retry_message(self) -> None:
        handler = HooksManager([_make_hook(command="echo 'fix it'; exit 2")])
        from vibe.core.config import SessionLoggingConfig
        from vibe.core.session.session_logger import SessionLogger

        logger = SessionLogger(SessionLoggingConfig(enabled=False), "test-id")
        events: list[BaseEvent | HookUserMessage] = []
        async for ev in handler.run(HookType.POST_AGENT_TURN, "sess", logger):
            events.append(ev)

        retry_msgs = [e for e in events if isinstance(e, HookUserMessage)]
        assert len(retry_msgs) == 1
        assert "fix it" in retry_msgs[0].content

        # Display message should be generic, not the stdout
        end_msgs = [
            e for e in events if isinstance(e, HookEndEvent) and e.content is not None
        ]
        assert any("retrying" in m.content.lower() for m in end_msgs if m.content)
        assert not any("fix it" in (m.content or "") for m in end_msgs)

    @pytest.mark.asyncio
    async def test_exit_2_without_output_emits_warning(self) -> None:
        handler = HooksManager([_make_hook(command="exit 2")])
        from vibe.core.config import SessionLoggingConfig
        from vibe.core.session.session_logger import SessionLogger

        logger = SessionLogger(SessionLoggingConfig(enabled=False), "test-id")
        events: list[BaseEvent | HookUserMessage] = []
        async for ev in handler.run(HookType.POST_AGENT_TURN, "sess", logger):
            events.append(ev)

        end_msgs = [e for e in events if isinstance(e, HookEndEvent)]
        assert len(end_msgs) == 1
        assert end_msgs[0].content == "Exited with code 2"

    @pytest.mark.asyncio
    async def test_max_retry_limit(self) -> None:
        handler = HooksManager([_make_hook(command="echo retry; exit 2")])
        from vibe.core.config import SessionLoggingConfig
        from vibe.core.session.session_logger import SessionLogger

        logger = SessionLogger(SessionLoggingConfig(enabled=False), "test-id")

        # Run 3 times (should get retry each time)
        for _ in range(3):
            events = [
                ev async for ev in handler.run(HookType.POST_AGENT_TURN, "sess", logger)
            ]
            assert any(isinstance(e, HookUserMessage) for e in events)

        # 4th time: max exceeded, no retry
        events = [
            ev async for ev in handler.run(HookType.POST_AGENT_TURN, "sess", logger)
        ]
        assert not any(isinstance(e, HookUserMessage) for e in events)
        # Should have error message about max retries
        error_events = [
            e
            for e in events
            if isinstance(e, HookEndEvent)
            and e.content
            and "exhausted" in e.content.lower()
        ]
        assert len(error_events) == 1

    @pytest.mark.asyncio
    async def test_warning_on_nonzero_exit(self) -> None:
        handler = HooksManager([_make_hook(command="echo warn; exit 1")])
        from vibe.core.config import SessionLoggingConfig
        from vibe.core.session.session_logger import SessionLogger

        logger = SessionLogger(SessionLoggingConfig(enabled=False), "test-id")
        events = [
            ev async for ev in handler.run(HookType.POST_AGENT_TURN, "sess", logger)
        ]

        warnings = [
            e
            for e in events
            if isinstance(e, HookEndEvent) and e.status == HookMessageSeverity.WARNING
        ]
        assert len(warnings) == 1
        assert warnings[0].content and "warn" in warnings[0].content

    @pytest.mark.asyncio
    async def test_warning_falls_back_to_stderr(self) -> None:
        hook = _make_hook(command="echo problem >&2; exit 1")
        handler = HooksManager([hook])
        from vibe.core.config import SessionLoggingConfig
        from vibe.core.session.session_logger import SessionLogger

        logger = SessionLogger(SessionLoggingConfig(enabled=False), "test-id")
        events = [
            ev async for ev in handler.run(HookType.POST_AGENT_TURN, "sess", logger)
        ]

        warnings = [
            e
            for e in events
            if isinstance(e, HookEndEvent) and e.status == HookMessageSeverity.WARNING
        ]
        assert len(warnings) == 1
        assert warnings[0].content and "problem" in warnings[0].content

    @pytest.mark.asyncio
    async def test_timeout_emits_warning(self) -> None:
        handler = HooksManager([_make_hook(command="sleep 60", timeout=0.5)])
        from vibe.core.config import SessionLoggingConfig
        from vibe.core.session.session_logger import SessionLogger

        logger = SessionLogger(SessionLoggingConfig(enabled=False), "test-id")
        events = [
            ev async for ev in handler.run(HookType.POST_AGENT_TURN, "sess", logger)
        ]

        warnings = [
            e
            for e in events
            if isinstance(e, HookEndEvent) and e.status == HookMessageSeverity.WARNING
        ]
        assert len(warnings) == 1
        assert warnings[0].content and "Timed out" in warnings[0].content


class TestAgentLoopIntegration:
    @pytest.mark.asyncio
    async def test_hooks_run_after_turn(self) -> None:
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
    async def test_hook_retry_reinjects_message(self) -> None:
        # First call: LLM responds. Hook requests retry with "fix this".
        # Second call (after retry injection): LLM responds again. Hook exits 0.
        backend = FakeBackend([
            [mock_llm_chunk(content="first response")],
            [mock_llm_chunk(content="second response")],
        ])

        # Create a script that exits 2 on first call, 0 on subsequent
        counter_file = Path.cwd() / ".hook_counter"
        script = (
            f'{sys.executable} -c "'
            f"from pathlib import Path; "
            f"p = Path({str(counter_file)!r}); "
            f"c = int(p.read_text()) if p.exists() else 0; "
            f"p.write_text(str(c + 1)); "
            f"import sys; "
            f"print('fix this'); "
            f"sys.exit(2 if c == 0 else 0)"
            f'"'
        )
        hooks = [_make_hook(name="retry-hook", command=script)]
        agent_loop = build_test_agent_loop(
            backend=backend, hook_config_result=HookConfigResult(hooks=hooks, issues=[])
        )

        events = [ev async for ev in agent_loop.act("hi")]

        # Should have two assistant events (two LLM turns)
        from vibe.core.types import AssistantEvent

        assistant_events = [e for e in events if isinstance(e, AssistantEvent)]
        assert len(assistant_events) == 2

        # Check that a retry user message was injected
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
