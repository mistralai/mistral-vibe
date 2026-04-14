"""Tests for deferred initialization: _complete_init, wait_for_init, integrate_mcp idempotency."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.core.config import MCPStdio
from vibe.core.tools.manager import ToolManager

# ---------------------------------------------------------------------------
# _complete_init
# ---------------------------------------------------------------------------


class TestCompleteInit:
    def test_success_sets_init_complete(self) -> None:
        loop = build_test_agent_loop(defer_heavy_init=True)
        assert not loop.is_initialized

        loop._complete_init()

        assert loop.is_initialized
        assert loop._init_error is None

    def test_failure_sets_init_complete_and_stores_error(self) -> None:
        loop = build_test_agent_loop(defer_heavy_init=True)
        error = RuntimeError("mcp boom")

        with patch.object(loop.tool_manager, "integrate_mcp", side_effect=error):
            loop._complete_init()

        assert loop.is_initialized
        assert loop._init_error is error

    def test_mcp_integration_internal_failure_sets_init_error(self) -> None:
        mcp_server = MCPStdio(name="test-server", transport="stdio", command="echo")
        config = build_test_vibe_config(mcp_servers=[mcp_server])
        loop = build_test_agent_loop(config=config, defer_heavy_init=True)

        with patch.object(
            loop.tool_manager._mcp_registry,
            "get_tools",
            side_effect=RuntimeError("mcp discovery boom"),
        ):
            loop._complete_init()

        assert loop.is_initialized
        assert isinstance(loop._init_error, RuntimeError)
        assert str(loop._init_error) == "mcp discovery boom"


# ---------------------------------------------------------------------------
# wait_for_init
# ---------------------------------------------------------------------------


class TestWaitForInit:
    @pytest.mark.asyncio
    async def test_returns_immediately_when_already_complete(self) -> None:
        loop = build_test_agent_loop(defer_heavy_init=True)
        loop._complete_init()

        await loop.wait_for_init()  # should not block

    @pytest.mark.asyncio
    async def test_waits_for_background_thread(self) -> None:
        loop = build_test_agent_loop(defer_heavy_init=True)

        thread = threading.Thread(target=loop._complete_init, daemon=True)
        thread.start()

        await loop.wait_for_init()
        thread.join(timeout=1)

        assert loop.is_initialized

    @pytest.mark.asyncio
    async def test_raises_stored_error(self) -> None:
        loop = build_test_agent_loop(defer_heavy_init=True)
        error = RuntimeError("init failed")

        with patch.object(loop.tool_manager, "integrate_mcp", side_effect=error):
            loop._complete_init()

        with pytest.raises(RuntimeError, match="init failed"):
            await loop.wait_for_init()

    @pytest.mark.asyncio
    async def test_raises_error_for_every_caller(self) -> None:
        loop = build_test_agent_loop(defer_heavy_init=True)
        error = RuntimeError("once only")

        with patch.object(loop.tool_manager, "integrate_mcp", side_effect=error):
            loop._complete_init()

        with pytest.raises(RuntimeError):
            await loop.wait_for_init()

        with pytest.raises(RuntimeError):
            await loop.wait_for_init()


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
