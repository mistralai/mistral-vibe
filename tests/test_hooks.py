"""Tests for lifecycle hook config models and HookManager."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.core.config._settings import HookEntry, HooksConfig, VibeConfig
from vibe.core.hooks import HookManager


# ── Config model tests ──


class TestHookEntry:
    def test_command_field(self) -> None:
        entry = HookEntry(command="echo hi")
        assert entry.command == "echo hi"


class TestHooksConfig:
    def test_defaults_to_empty_lists(self) -> None:
        config = HooksConfig()
        assert config.session_start == []
        assert config.user_prompt_submit == []
        assert config.pre_tool_use == []
        assert config.post_tool_use == []
        assert config.turn_end == []

    def test_single_hook_per_event(self) -> None:
        config = HooksConfig(
            session_start=[HookEntry(command="echo start")],
        )
        assert len(config.session_start) == 1
        assert config.session_start[0].command == "echo start"

    def test_multiple_hooks_per_event(self) -> None:
        config = HooksConfig(
            turn_end=[
                HookEntry(command="cmd1"),
                HookEntry(command="cmd2"),
            ],
        )
        assert len(config.turn_end) == 2

    def test_all_events_populated(self) -> None:
        config = HooksConfig(
            session_start=[HookEntry(command="a")],
            user_prompt_submit=[HookEntry(command="b")],
            pre_tool_use=[HookEntry(command="c")],
            post_tool_use=[HookEntry(command="d")],
            turn_end=[HookEntry(command="e")],
        )
        assert len(config.session_start) == 1
        assert len(config.user_prompt_submit) == 1
        assert len(config.pre_tool_use) == 1
        assert len(config.post_tool_use) == 1
        assert len(config.turn_end) == 1


class TestVibeConfigHooks:
    def test_default_hooks_empty(self) -> None:
        config = VibeConfig()
        assert isinstance(config.hooks, HooksConfig)
        assert config.hooks.session_start == []

    def test_hooks_from_kwargs(self) -> None:
        hooks = HooksConfig(session_start=[HookEntry(command="test")])
        config = VibeConfig(hooks=hooks)
        assert config.hooks.session_start[0].command == "test"


# ── HookManager unit tests ──


def _make_manager(
    event: str = "session_start", commands: list[str] | None = None
) -> HookManager:
    """Create a HookManager with hooks for the given event."""
    hooks = commands or ["cat"]
    entries = [HookEntry(command=c) for c in hooks]
    config = HooksConfig(**{event: entries})
    return HookManager(config=config, session_id="test-sid-123", cwd="/tmp/test")


def _mock_proc(returncode: int = 0, stderr: bytes = b"") -> AsyncMock:
    """Create a mock async subprocess."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


class TestHookManagerPayloads:
    @pytest.mark.asyncio
    async def test_session_start_payload(self) -> None:
        mgr = _make_manager("session_start", ["cat"])
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_shell", return_value=proc):
            mgr.emit_session_start()
            await mgr.drain()
        payload = json.loads(proc.communicate.call_args[1]["input"])
        assert payload["hook_event_name"] == "session_start"
        assert payload["session_id"] == "test-sid-123"
        assert payload["cwd"] == "/tmp/test"

    @pytest.mark.asyncio
    async def test_user_prompt_submit_payload(self) -> None:
        mgr = _make_manager("user_prompt_submit", ["cat"])
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_shell", return_value=proc):
            mgr.emit_user_prompt_submit("Fix the bug")
            await mgr.drain()
        payload = json.loads(proc.communicate.call_args[1]["input"])
        assert payload["hook_event_name"] == "user_prompt_submit"
        assert payload["prompt"] == "Fix the bug"

    @pytest.mark.asyncio
    async def test_pre_tool_use_payload(self) -> None:
        mgr = _make_manager("pre_tool_use", ["cat"])
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_shell", return_value=proc):
            mgr.emit_pre_tool_use("write_file", {"path": "/foo.py"})
            await mgr.drain()
        payload = json.loads(proc.communicate.call_args[1]["input"])
        assert payload["hook_event_name"] == "pre_tool_use"
        assert payload["tool_name"] == "write_file"
        assert payload["tool_input"] == {"path": "/foo.py"}

    @pytest.mark.asyncio
    async def test_post_tool_use_success_payload(self) -> None:
        mgr = _make_manager("post_tool_use", ["cat"])
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_shell", return_value=proc):
            mgr.emit_post_tool_use(
                "write_file", {"path": "/foo.py"}, "success",
                tool_response={"result": "ok"},
            )
            await mgr.drain()
        payload = json.loads(proc.communicate.call_args[1]["input"])
        assert payload["hook_event_name"] == "post_tool_use"
        assert payload["tool_outcome"] == "success"
        assert payload["tool_response"] == {"result": "ok"}
        assert payload["tool_error"] is None

    @pytest.mark.asyncio
    async def test_post_tool_use_failed_payload(self) -> None:
        mgr = _make_manager("post_tool_use", ["cat"])
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_shell", return_value=proc):
            mgr.emit_post_tool_use(
                "bash", {"command": "bad"}, "failed",
                tool_error="boom",
            )
            await mgr.drain()
        payload = json.loads(proc.communicate.call_args[1]["input"])
        assert payload["tool_outcome"] == "failed"
        assert payload["tool_response"] is None
        assert payload["tool_error"] == "boom"

    @pytest.mark.asyncio
    async def test_turn_end_payload(self) -> None:
        mgr = _make_manager("turn_end", ["cat"])
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_shell", return_value=proc):
            mgr.emit_turn_end()
            await mgr.drain()
        payload = json.loads(proc.communicate.call_args[1]["input"])
        assert payload["hook_event_name"] == "turn_end"
        assert "session_id" in payload
        assert "cwd" in payload


class TestHookManagerBehavior:
    @pytest.mark.asyncio
    async def test_no_hooks_configured_no_subprocess(self) -> None:
        mgr = HookManager(config=HooksConfig(), session_id="s", cwd="/tmp")
        with patch("asyncio.create_subprocess_shell") as mock_shell:
            mgr.emit_session_start()
            await mgr.drain()
        mock_shell.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_hooks_per_event(self) -> None:
        mgr = _make_manager("session_start", ["cmd1", "cmd2"])
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_shell", return_value=proc) as mock_shell:
            mgr.emit_session_start()
            await mgr.drain()
        assert mock_shell.call_count == 2

    @pytest.mark.asyncio
    async def test_hook_nonzero_exit_does_not_raise(self) -> None:
        mgr = _make_manager("session_start", ["false"])
        proc = _mock_proc(returncode=1)
        with patch("asyncio.create_subprocess_shell", return_value=proc):
            mgr.emit_session_start()
            await mgr.drain()  # should not raise

    @pytest.mark.asyncio
    async def test_hook_subprocess_error_does_not_raise(self) -> None:
        mgr = _make_manager("session_start", ["bad"])
        with patch("asyncio.create_subprocess_shell", side_effect=OSError("fail")):
            mgr.emit_session_start()
            await mgr.drain()  # should not raise

    @pytest.mark.asyncio
    async def test_hook_stderr_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        mgr = _make_manager("session_start", ["cat"])
        proc = _mock_proc(stderr=b"some warning")
        with patch("asyncio.create_subprocess_shell", return_value=proc):
            with caplog.at_level(logging.DEBUG, logger="vibe.core.hooks"):
                mgr.emit_session_start()
                await mgr.drain()
        assert "some warning" in caplog.text

    @pytest.mark.asyncio
    async def test_completed_tasks_removed_from_set(self) -> None:
        mgr = _make_manager("session_start", ["cat"])
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_shell", return_value=proc):
            mgr.emit_session_start()
            await mgr.drain()
        assert len(mgr._tasks) == 0

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self) -> None:
        mgr = _make_manager("session_start", ["sleep 999"])
        proc = AsyncMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        proc.kill = MagicMock()
        proc.wait = AsyncMock()
        with patch("asyncio.create_subprocess_shell", return_value=proc):
            with patch("vibe.core.hooks.HOOK_TIMEOUT_SECONDS", 0.01):
                mgr.emit_session_start()
                await mgr.drain()
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_path_object_in_payload_serialized(self) -> None:
        mgr = _make_manager("pre_tool_use", ["cat"])
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_shell", return_value=proc):
            mgr.emit_pre_tool_use("write_file", {"path": Path("/foo/bar.py")})
            await mgr.drain()
        payload = json.loads(proc.communicate.call_args[1]["input"])
        assert payload["tool_input"]["path"] == str(Path("/foo/bar.py"))

    @pytest.mark.asyncio
    async def test_serialization_failure_skips_hook(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        mgr = _make_manager("pre_tool_use", ["cat"])

        class Unserializable:
            def __str__(self) -> str:
                raise RuntimeError("cannot serialize")
            def __repr__(self) -> str:
                raise RuntimeError("cannot serialize")

        with patch("asyncio.create_subprocess_shell") as mock_shell:
            with caplog.at_level(logging.ERROR, logger="vibe.core.hooks"):
                mgr.emit_pre_tool_use("tool", {"bad": Unserializable()})
                await mgr.drain()
        mock_shell.assert_not_called()
