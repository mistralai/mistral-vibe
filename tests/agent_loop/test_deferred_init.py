"""Tests for deferred initialization: _complete_init, _wait_for_init, integrate_mcp idempotency."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_connector_registry import FakeConnectorRegistry
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.core import agent_loop as agent_loop_module
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import MCPStdio
from vibe.core.telemetry.types import TerminalEmulator
from vibe.core.tools.manager import ToolManager
from vibe.core.tools.mcp import AuthStatus
from vibe.core.tools.remote import RemoteTool


def _build_uninitiated_loop(**kwargs):
    """Build a test loop with defer_heavy_init=True but without auto-starting the init thread."""
    with patch.object(AgentLoop, "_start_deferred_init"):
        return build_test_agent_loop(defer_heavy_init=True, **kwargs)


# ---------------------------------------------------------------------------
# _complete_init
# ---------------------------------------------------------------------------


def _run_init(loop: AgentLoop) -> None:
    """Run _complete_init in a thread (matching production behavior) and wait."""
    thread = threading.Thread(target=loop._complete_init, daemon=True)
    loop._deferred_init_thread = thread
    thread.start()
    thread.join()


class TestCompleteInit:
    def test_success_sets_init_complete(self) -> None:
        loop = _build_uninitiated_loop()
        assert not loop.is_initialized

        _run_init(loop)

        assert loop.is_initialized
        assert loop._init_error is None

    def test_failure_sets_init_complete_and_stores_error(self) -> None:
        loop = _build_uninitiated_loop()
        error = RuntimeError("mcp boom")

        with patch.object(loop.tool_manager, "integrate_all", side_effect=error):
            _run_init(loop)

        assert loop.is_initialized
        assert loop._init_error is error

    def test_mcp_failure_sets_init_error(self) -> None:
        mcp_server = MCPStdio(name="test-server", transport="stdio", command="echo")
        config = build_test_vibe_config(mcp_servers=[mcp_server])
        loop = _build_uninitiated_loop(config=config)

        with patch.object(
            loop.tool_manager,
            "integrate_all",
            side_effect=RuntimeError("mcp discovery boom"),
        ):
            _run_init(loop)

        assert loop.is_initialized
        assert isinstance(loop._init_error, RuntimeError)
        assert str(loop._init_error) == "mcp discovery boom"

    def test_delays_connector_registry_until_deferred_init(self) -> None:
        config = build_test_vibe_config(enable_connectors=True)
        with patch.object(AgentLoop, "_start_deferred_init"):
            loop = AgentLoop(
                config=config, backend=FakeBackend(), defer_heavy_init=True
            )

        assert loop.connector_registry is None

        with patch.object(loop.tool_manager, "integrate_all"):
            _run_init(loop)

        assert loop.connector_registry is not None


# ---------------------------------------------------------------------------
# wait_until_ready
# ---------------------------------------------------------------------------


class TestWaitForInit:
    @pytest.mark.asyncio
    async def test_returns_immediately_when_already_complete(self) -> None:
        loop = build_test_agent_loop(defer_heavy_init=True)

        await loop.wait_until_ready()  # should not block

        assert loop.is_initialized

    @pytest.mark.asyncio
    async def test_waits_for_background_thread(self) -> None:
        loop = build_test_agent_loop(defer_heavy_init=True)

        await loop.wait_until_ready()

        assert loop.is_initialized

    @pytest.mark.asyncio
    async def test_raises_stored_error(self) -> None:
        loop = _build_uninitiated_loop()
        error = RuntimeError("init failed")

        with patch.object(loop.tool_manager, "integrate_all", side_effect=error):
            loop._complete_init()

        with pytest.raises(RuntimeError, match="init failed"):
            await loop.wait_until_ready()

    @pytest.mark.asyncio
    async def test_raises_error_for_every_caller(self) -> None:
        loop = _build_uninitiated_loop()
        error = RuntimeError("once only")

        with patch.object(loop.tool_manager, "integrate_all", side_effect=error):
            loop._complete_init()

        with pytest.raises(RuntimeError):
            await loop.wait_until_ready()

        with pytest.raises(RuntimeError):
            await loop.wait_until_ready()


# ---------------------------------------------------------------------------
# integrate_mcp idempotency
# ---------------------------------------------------------------------------


class TestIntegrateMcpIdempotency:
    def test_second_call_is_noop(self) -> None:
        mcp_server = MCPStdio(name="test-server", transport="stdio", command="echo")
        config = build_test_vibe_config(mcp_servers=[mcp_server])
        registry = FakeMCPRegistry()
        manager = ToolManager(lambda: config, mcp_registry=registry, defer_mcp=True)

        manager.integrate_mcp()
        tools_after_first = dict(manager.registered_tools)

        # Spy on the registry to ensure get_tools is not called again.
        registry.get_tools = MagicMock(wraps=registry.get_tools)
        manager.integrate_mcp()

        registry.get_tools.assert_not_called()
        assert manager.registered_tools == tools_after_first

    def test_flag_not_set_when_no_servers(self) -> None:
        config = build_test_vibe_config(mcp_servers=[])
        manager = ToolManager(lambda: config, defer_mcp=True)

        manager.integrate_mcp()

        # No servers means the method returns early without setting the flag,
        # so a future call with servers would still run discovery.
        assert not manager._mcp_integrated

    def test_no_servers_syncs_shared_registry_status(self) -> None:
        config = build_test_vibe_config(
            mcp_servers=[MCPStdio(name="srv", transport="stdio", command="echo")]
        )
        registry = FakeMCPRegistry()
        manager = ToolManager(lambda: config, mcp_registry=registry, defer_mcp=True)

        manager.integrate_mcp()
        assert registry.status() == {"srv": AuthStatus.STDIO}

        config = build_test_vibe_config(mcp_servers=[])
        manager = ToolManager(lambda: config, mcp_registry=registry, defer_mcp=True)
        manager.integrate_mcp()

        assert registry.status() == {}
        assert not manager._mcp_integrated


class TestRefreshRemoteTools:
    @pytest.mark.asyncio
    async def test_refresh_rediscovers_mcp_and_connector_tools(self) -> None:
        mcp_server = MCPStdio(name="srv", transport="stdio", command="echo")
        config = build_test_vibe_config(mcp_servers=[mcp_server])
        registry = FakeMCPRegistry()
        registry.get_tools_async = AsyncMock(wraps=registry.get_tools_async)
        connector_registry = FakeConnectorRegistry({
            "alpha": [RemoteTool(name="search", description="Search alpha")]
        })
        manager = ToolManager(
            lambda: config,
            mcp_registry=registry,
            connector_registry=connector_registry,
            defer_mcp=True,
        )

        await manager.refresh_remote_tools_async()

        assert "srv_fake_tool" in manager.registered_tools
        assert "connector_alpha_search" in manager.registered_tools

        connector_registry._fake_connectors = {
            "beta": [RemoteTool(name="list", description="List beta")]
        }

        await manager.refresh_remote_tools_async()

        assert registry.get_tools_async.await_count == 2
        assert "srv_fake_tool" in manager.registered_tools
        assert "connector_alpha_search" not in manager.registered_tools
        assert "connector_beta_list" in manager.registered_tools


class TestDeferredInitPublicMethods:
    @pytest.mark.asyncio
    async def test_act_waits_for_deferred_init(self) -> None:
        loop = build_test_agent_loop(
            defer_heavy_init=True, backend=FakeBackend(mock_llm_chunk(content="hello"))
        )

        events = [event async for event in loop.act("Hello")]

        assert loop.is_initialized
        assert [event.content for event in events if hasattr(event, "content")][
            -1
        ] == "hello"

    @pytest.mark.asyncio
    async def test_reload_with_initial_messages_waits_for_deferred_init(self) -> None:
        loop = build_test_agent_loop(defer_heavy_init=True)

        await loop.reload_with_initial_messages()

        assert loop.is_initialized

    @pytest.mark.asyncio
    async def test_reload_creates_shared_mcp_registry_after_servers_are_added(
        self,
    ) -> None:
        loop = build_test_agent_loop(defer_heavy_init=True, mcp_registry=None)
        await loop.wait_until_ready()
        assert loop.mcp_registry is None

        mcp_server = MCPStdio(name="srv", transport="stdio", command="echo")
        config = build_test_vibe_config(mcp_servers=[mcp_server])
        registry = FakeMCPRegistry()

        with (
            patch.object(AgentLoop, "_create_mcp_registry", return_value=registry),
            patch.object(ToolManager, "integrate_all"),
        ):
            await loop.reload_with_initial_messages(base_config=config)

        assert loop.mcp_registry is registry
        assert loop.tool_manager._mcp_registry is registry

    @pytest.mark.asyncio
    async def test_switch_agent_waits_for_deferred_init(self) -> None:
        loop = build_test_agent_loop(defer_heavy_init=True)

        await loop.switch_agent("plan")

        assert loop.is_initialized
        assert loop.agent_profile.name == "plan"

    @pytest.mark.asyncio
    async def test_clear_history_waits_for_deferred_init(self) -> None:
        loop = build_test_agent_loop(
            defer_heavy_init=True, backend=FakeBackend(mock_llm_chunk(content="hello"))
        )
        [_ async for _ in loop.act("Hello")]

        await loop.clear_history()

        assert loop.is_initialized
        assert len(loop.messages) == 1

    @pytest.mark.asyncio
    async def test_compact_waits_for_deferred_init(self) -> None:
        loop = build_test_agent_loop(
            defer_heavy_init=True,
            backend=FakeBackend([
                [mock_llm_chunk(content="hello")],
                [mock_llm_chunk(content="summary")],
            ]),
        )
        [_ async for _ in loop.act("Hello")]

        summary = await loop.compact()

        assert loop.is_initialized
        assert summary == "summary"

    @pytest.mark.asyncio
    async def test_inject_user_context_waits_for_deferred_init(self) -> None:
        loop = build_test_agent_loop(defer_heavy_init=True)

        await loop.inject_user_context("context")

        assert loop.is_initialized
        assert loop.messages[-1].content == "context"


# ---------------------------------------------------------------------------
# start_initialize_experiments / wait_until_ready experiment gating
# ---------------------------------------------------------------------------


class TestStartInitializeExperiments:
    @pytest.mark.asyncio
    async def test_does_not_block_caller(self) -> None:
        loop = build_test_agent_loop()
        gate = asyncio.Event()

        async def slow_init() -> None:
            await gate.wait()

        with patch.object(loop, "initialize_experiments", side_effect=slow_init):
            loop.start_initialize_experiments()

            task = loop._experiments_task
            assert task is not None
            assert not task.done()

            gate.set()
            await task

    @pytest.mark.asyncio
    async def test_is_idempotent(self) -> None:
        loop = build_test_agent_loop()
        init_mock = AsyncMock()

        with patch.object(loop, "initialize_experiments", new=init_mock):
            loop.start_initialize_experiments()
            first_task = loop._experiments_task
            loop.start_initialize_experiments()
            second_task = loop._experiments_task

            assert first_task is second_task
            assert first_task is not None
            await first_task

        assert init_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_sets_pending_telemetry_flags(self) -> None:
        loop = build_test_agent_loop()

        with patch.object(loop, "initialize_experiments", new=AsyncMock()):
            loop.start_initialize_experiments()

            assert loop._pending_new_session_telemetry is True
            assert loop._ready_telemetry_pending is True

            task = loop._experiments_task
            assert task is not None
            await task

    @pytest.mark.asyncio
    async def test_refreshes_system_prompt_when_experiments_update(self) -> None:
        loop = build_test_agent_loop(terminal_emulator=TerminalEmulator.VSCODE)
        refresh_mock = AsyncMock()
        init_mock = AsyncMock(return_value=True)

        with (
            patch.object(
                agent_loop_module, "session_initialize_experiments", new=init_mock
            ),
            patch.object(loop, "refresh_system_prompt", new=refresh_mock),
        ):
            await loop.initialize_experiments()

        refresh_mock.assert_awaited_once()
        init_mock.assert_awaited_once()
        init_args = init_mock.await_args
        assert init_args is not None
        assert init_args.kwargs["terminal_emulator"] is TerminalEmulator.VSCODE

    def test_new_session_telemetry_uses_provided_terminal_emulator(self) -> None:
        loop = build_test_agent_loop(terminal_emulator=TerminalEmulator.VSCODE)
        send_new_session = MagicMock()

        with patch.object(
            loop.telemetry_client, "send_new_session", new=send_new_session
        ):
            loop.emit_new_session_telemetry()

        assert (
            send_new_session.call_args.kwargs["terminal_emulator"]
            is TerminalEmulator.VSCODE
        )

    @pytest.mark.asyncio
    async def test_does_not_refresh_system_prompt_when_experiments_unchanged(
        self,
    ) -> None:
        loop = build_test_agent_loop()
        refresh_mock = AsyncMock()

        with (
            patch.object(
                agent_loop_module,
                "session_initialize_experiments",
                new=AsyncMock(return_value=False),
            ),
            patch.object(loop, "refresh_system_prompt", new=refresh_mock),
        ):
            await loop.initialize_experiments()

        refresh_mock.assert_not_awaited()


class TestWaitUntilReadyJoinsExperiments:
    @pytest.mark.asyncio
    async def test_joins_in_flight_task(self) -> None:
        loop = build_test_agent_loop()
        gate = asyncio.Event()
        completed = False

        async def slow_init() -> None:
            nonlocal completed
            await gate.wait()
            completed = True

        with patch.object(loop, "initialize_experiments", side_effect=slow_init):
            loop.start_initialize_experiments()

            async def release() -> None:
                await asyncio.sleep(0.01)
                gate.set()

            asyncio.create_task(release())
            await loop.wait_until_ready()

            assert completed is True
            task = loop._experiments_task
            assert task is not None
            assert task.done()

    @pytest.mark.asyncio
    async def test_emits_new_session_telemetry_once(self) -> None:
        loop = build_test_agent_loop()
        emit_new_session = MagicMock()

        with (
            patch.object(loop, "initialize_experiments", new=AsyncMock()),
            patch.object(loop, "emit_new_session_telemetry", new=emit_new_session),
        ):
            loop.start_initialize_experiments()
            await loop.wait_until_ready()
            await loop.wait_until_ready()

        emit_new_session.assert_called_once()
        assert loop._pending_new_session_telemetry is False

    @pytest.mark.asyncio
    async def test_emits_ready_telemetry_when_only_experiments_deferred(self) -> None:
        loop = build_test_agent_loop()
        emit_ready = MagicMock()

        with (
            patch.object(loop, "initialize_experiments", new=AsyncMock()),
            patch.object(loop, "emit_ready_telemetry", new=emit_ready),
        ):
            loop.start_initialize_experiments()
            await loop.wait_until_ready()
            await loop.wait_until_ready()

        emit_ready.assert_called_once()
        ((duration,), _) = emit_ready.call_args
        assert isinstance(duration, int)
        assert duration >= 0
        assert loop._ready_telemetry_pending is False

    @pytest.mark.asyncio
    async def test_does_not_emit_new_session_when_only_hydrating(self) -> None:
        loop = build_test_agent_loop()
        emit_new_session = MagicMock()

        with (
            patch.object(loop, "hydrate_experiments_from_session", new=AsyncMock()),
            patch.object(loop, "emit_new_session_telemetry", new=emit_new_session),
        ):
            await loop.hydrate_experiments_from_session()
            await loop.wait_until_ready()

        emit_new_session.assert_not_called()
        assert loop._pending_new_session_telemetry is False

    @pytest.mark.asyncio
    async def test_no_op_when_nothing_deferred(self) -> None:
        loop = build_test_agent_loop()
        emit_ready = MagicMock()
        emit_new_session = MagicMock()

        with (
            patch.object(loop, "emit_ready_telemetry", new=emit_ready),
            patch.object(loop, "emit_new_session_telemetry", new=emit_new_session),
        ):
            await loop.wait_until_ready()

        emit_ready.assert_not_called()
        emit_new_session.assert_not_called()


class TestACloseCancelsExperimentsTask:
    @pytest.mark.asyncio
    async def test_cancels_in_flight_task(self) -> None:
        loop = build_test_agent_loop()
        gate = asyncio.Event()

        async def never_completing() -> None:
            await gate.wait()

        with patch.object(loop, "initialize_experiments", side_effect=never_completing):
            loop.start_initialize_experiments()
            task = loop._experiments_task
            assert task is not None
            assert not task.done()

            await loop.aclose()

            assert task.done()
            assert task.cancelled()

    @pytest.mark.asyncio
    async def test_does_not_cancel_completed_task(self) -> None:
        loop = build_test_agent_loop()

        with patch.object(loop, "initialize_experiments", new=AsyncMock()):
            loop.start_initialize_experiments()
            task = loop._experiments_task
            assert task is not None
            await task

            await loop.aclose()

            assert task.done()
            assert not task.cancelled()


class TestCycleAgentDuringInit:
    @pytest.mark.asyncio
    async def test_shift_tab_during_experiments_init_does_not_crash(self) -> None:
        """Regression: shift+tab during init crashed with
        RuntimeError("await wasn't used with future").

        _cycle_agent ran switch_agent via asyncio.run() in a thread worker,
        creating a second event loop.  wait_until_ready then tried to await
        _experiments_task (owned by the main Textual loop) from that new loop.

        The fix uses asyncio.run_coroutine_threadsafe() to schedule on the
        main loop instead.  This test presses shift+tab while experiments
        are still initializing and asserts the worker completes without error.
        """
        from tests.conftest import build_test_vibe_app

        gate = asyncio.Event()

        async def slow_init() -> None:
            await gate.wait()

        agent_loop = build_test_agent_loop()
        app = build_test_vibe_app(agent_loop=agent_loop)

        async with app.run_test() as pilot:
            with patch.object(
                agent_loop, "initialize_experiments", side_effect=slow_init
            ):
                agent_loop._experiments_task = None  # reset so start_ re-fires
                agent_loop.start_initialize_experiments()

                assert agent_loop._experiments_task is not None
                assert not agent_loop._experiments_task.done()

                # Press shift+tab while experiments are still running.
                await pilot.press("shift+tab")
                await pilot.pause(0.05)

                # Unblock experiments so switch_agent can complete.
                gate.set()

                # wait_for_complete raises WorkerFailed if the thread worker
                # crashed — which is exactly what happened before the fix.
                await pilot.app.workers.wait_for_complete()

            assert agent_loop.agent_profile.name == "plan"


class TestActGatesOnExperiments:
    @pytest.mark.asyncio
    async def test_act_awaits_experiments_before_llm_call(self) -> None:
        loop = build_test_agent_loop(
            backend=FakeBackend(mock_llm_chunk(content="hello"))
        )
        gate = asyncio.Event()
        finished_init = False

        async def slow_init() -> None:
            nonlocal finished_init
            await gate.wait()
            finished_init = True

        with patch.object(loop, "initialize_experiments", side_effect=slow_init):
            loop.start_initialize_experiments()

            async def release() -> None:
                await asyncio.sleep(0.01)
                gate.set()

            asyncio.create_task(release())
            events = [event async for event in loop.act("Hello")]

            assert finished_init is True
            assert any(getattr(event, "content", None) == "hello" for event in events)
