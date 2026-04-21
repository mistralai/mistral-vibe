"""Tests for deferred initialization: _complete_init, _wait_for_init, integrate_mcp idempotency."""

from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_connector_registry import FakeConnectorRegistry
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import MCPStdio
from vibe.core.tools.manager import ToolManager
from vibe.core.tools.mcp.tools import RemoteTool


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
            loop.tool_manager._mcp_registry,
            "get_tools_async",
            side_effect=RuntimeError("mcp discovery boom"),
        ):
            _run_init(loop)

        assert loop.is_initialized
        assert isinstance(loop._init_error, RuntimeError)
        assert str(loop._init_error) == "mcp discovery boom"


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
