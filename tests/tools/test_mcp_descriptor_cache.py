from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from vibe.core.config import MCPHttp
from vibe.core.tools.mcp.descriptor_cache import (
    LegacyMCPDescriptorCache,
    descriptor_cache_key,
)
from vibe.core.tools.mcp.registry import MCPRegistry
from vibe.core.tools.remote import RemoteTool


def _now() -> datetime:
    return datetime(2026, 8, 20, 12, tzinfo=UTC)


def _key(suffix: str = "one") -> str:
    return descriptor_cache_key(
        server_fingerprint=f"fingerprint-{suffix}",
        descriptor_revision=f"authorization-{suffix}",
    )


def _server(name: str = "linear") -> MCPHttp:
    return MCPHttp(name=name, transport="http", url=f"https://{name}.example.test/mcp")


@pytest.mark.asyncio
async def test_legacy_descriptor_cache_round_trip_uses_non_sliding_ttl(
    tmp_path: Path,
) -> None:
    cache = LegacyMCPDescriptorCache(tmp_path, ttl_s=10)
    descriptor = RemoteTool(
        name="search", input_schema={"type": "object", "properties": {}}
    )
    assert await cache.write(
        _key(), source_name="linear", descriptors=(descriptor,), now=_now()
    )

    fresh = await cache.read(
        _key(), source_name="linear", now=_now() + timedelta(seconds=9)
    )
    expired = await cache.read(
        _key(), source_name="linear", now=_now() + timedelta(seconds=10)
    )

    assert fresh is not None
    assert fresh.descriptors == (descriptor,)
    assert fresh.discovered_at == _now()
    assert fresh.last_used_at == _now() + timedelta(seconds=9)
    assert expired is None


@pytest.mark.asyncio
async def test_legacy_descriptor_cache_rejects_corrupt_mismatched_and_future_records(
    tmp_path: Path,
) -> None:
    cache = LegacyMCPDescriptorCache(tmp_path)
    descriptor = (RemoteTool(name="tool"),)
    assert await cache.write(
        _key("valid"), source_name="valid", descriptors=descriptor, now=_now()
    )
    assert await cache.write(
        _key("future"),
        source_name="future",
        descriptors=descriptor,
        now=_now() + timedelta(seconds=1),
    )
    (tmp_path / ("0" * 64 + ".json")).write_text("not-json")

    valid = await cache.read(_key("valid"), source_name="valid", now=_now())
    mismatch = await cache.read(_key("valid"), source_name="other", now=_now())
    future = await cache.read(_key("future"), source_name="future", now=_now())

    assert valid is not None
    assert mismatch is None
    assert future is None


@pytest.mark.asyncio
async def test_zero_ttl_disables_legacy_descriptor_cache(tmp_path: Path) -> None:
    cache = LegacyMCPDescriptorCache(tmp_path, ttl_s=0)

    written = await cache.write(
        _key(), source_name="linear", descriptors=(RemoteTool(name="tool"),), now=_now()
    )
    loaded = await cache.read(_key(), source_name="linear", now=_now())

    assert written is False
    assert loaded is None
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("ttl", [-1.0, float("inf"), float("nan")])
def test_legacy_descriptor_cache_rejects_invalid_ttl(
    tmp_path: Path, ttl: float
) -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        LegacyMCPDescriptorCache(tmp_path, ttl_s=ttl)


@pytest.mark.asyncio
async def test_legacy_descriptor_cache_evicts_least_recently_used_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("vibe.core.tools.mcp.descriptor_cache._MAX_FILES", 2)
    cache = LegacyMCPDescriptorCache(tmp_path)
    descriptor = (RemoteTool(name="tool"),)
    for offset, name in enumerate(("old", "middle", "new")):
        assert await cache.write(
            _key(name),
            source_name=name,
            descriptors=descriptor,
            now=_now() + timedelta(seconds=offset),
        )

    assert (
        await cache.read(
            _key("old"), source_name="old", now=_now() + timedelta(seconds=3)
        )
        is None
    )
    assert (
        await cache.read(
            _key("middle"), source_name="middle", now=_now() + timedelta(seconds=3)
        )
        is not None
    )
    assert (
        await cache.read(
            _key("new"), source_name="new", now=_now() + timedelta(seconds=3)
        )
        is not None
    )


@pytest.mark.asyncio
async def test_legacy_descriptor_cache_serializes_concurrent_readers_and_writers(
    tmp_path: Path,
) -> None:
    cache = LegacyMCPDescriptorCache(tmp_path)
    descriptor = (RemoteTool(name="tool"),)

    results = await asyncio.gather(
        *(
            cache.write(_key(), source_name="linear", descriptors=descriptor)
            for _ in range(8)
        )
    )
    records = await asyncio.gather(
        *(cache.read(_key(), source_name="linear") for _ in range(8))
    )

    assert all(results)
    assert all(record is not None for record in records)
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.asyncio
async def test_legacy_descriptor_cache_writes_when_fchmod_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A Windows-like OS module without file-descriptor chmod support.
    *Do*: Write and read a legacy descriptor-cache record.
    *Assert*: Atomic persistence succeeds through the portable path-based fallback.
    """
    # Prepare
    monkeypatch.delattr(os, "fchmod", raising=False)
    cache = LegacyMCPDescriptorCache(tmp_path)
    descriptors = (RemoteTool(name="tool"),)

    # Do
    written = await cache.write(
        _key(), source_name="linear", descriptors=descriptors, now=_now()
    )
    loaded = await cache.read(_key(), source_name="linear", now=_now())

    # Assert
    assert written is True
    assert loaded is not None
    assert loaded.descriptors == descriptors


@pytest.mark.asyncio
async def test_legacy_registry_reuses_persistent_descriptors_across_process_instances(
    tmp_path: Path,
) -> None:
    server = _server()
    first = MCPRegistry(descriptor_cache_root=tmp_path)
    with patch(
        "vibe.core.tools.mcp.registry.list_tools_http",
        new=AsyncMock(return_value=[RemoteTool(name="search")]),
    ):
        assert set(await first.get_tools_async([server])) == {"linear_search"}

    second = MCPRegistry(descriptor_cache_root=tmp_path)
    discovery = AsyncMock(side_effect=AssertionError("cache hit performed MCP I/O"))
    with patch("vibe.core.tools.mcp.registry.list_tools_http", new=discovery):
        assert set(await second.get_tools_async([server])) == {"linear_search"}

    discovery.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_registry_discovers_only_persistent_partial_misses(
    tmp_path: Path,
) -> None:
    cached = _server("cached")
    missing = _server("missing")
    first = MCPRegistry(descriptor_cache_root=tmp_path)
    with patch(
        "vibe.core.tools.mcp.registry.list_tools_http",
        new=AsyncMock(return_value=[RemoteTool(name="cached_tool")]),
    ):
        await first.get_tools_async([cached])

    second = MCPRegistry(descriptor_cache_root=tmp_path)
    discovery = AsyncMock(return_value=[RemoteTool(name="fresh_tool")])
    with patch("vibe.core.tools.mcp.registry.list_tools_http", new=discovery):
        tools = await second.get_tools_async([cached, missing])

    assert set(tools) == {"cached_cached_tool", "missing_fresh_tool"}
    discovery.assert_awaited_once()
    await_args = discovery.await_args
    assert await_args is not None
    assert await_args.args[0] == missing.url


@pytest.mark.asyncio
async def test_failed_forced_refresh_never_reuses_persistent_stale_descriptors(
    tmp_path: Path,
) -> None:
    server = _server()
    registry = MCPRegistry(descriptor_cache_root=tmp_path)
    discovery = AsyncMock(
        side_effect=[
            [RemoteTool(name="old")],
            ConnectionError("down"),
            [RemoteTool(name="new")],
        ]
    )
    with patch("vibe.core.tools.mcp.registry.list_tools_http", new=discovery):
        assert set(await registry.get_tools_async([server])) == {"linear_old"}
        registry.invalidate(server.name)
        assert await registry.get_tools_async([server]) == {}
        assert set(await registry.get_tools_async([server])) == {"linear_new"}

    assert discovery.await_count == 3


@pytest.mark.asyncio
async def test_legacy_descriptor_cache_keeps_both_schemas_across_repeated_reads(
    tmp_path: Path,
) -> None:
    """A read rewrites the entry it read, so a lossy decode erases the schemas.

    Plugin tool routes are fingerprinted over both schemas, so a cache hit that
    answered with an empty one would report every restored route as drifted.
    """
    descriptor = RemoteTool.model_validate({
        "name": "search",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
        "outputSchema": {"type": "object", "properties": {"hit": {"type": "string"}}},
    })
    cache = LegacyMCPDescriptorCache(tmp_path)
    assert await cache.write(_key(), source_name="linear", descriptors=(descriptor,))

    await cache.read(_key(), source_name="linear")
    reread = await cache.read(_key(), source_name="linear")

    assert reread is not None
    assert reread.descriptors == (descriptor,)


@pytest.mark.asyncio
async def test_legacy_registry_serves_the_discovered_schemas_from_the_cache(
    tmp_path: Path,
) -> None:
    server = _server()
    descriptor = RemoteTool.model_validate({
        "name": "search",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
        "outputSchema": {"type": "object", "properties": {"hit": {"type": "string"}}},
    })
    first = MCPRegistry(descriptor_cache_root=tmp_path)
    with patch(
        "vibe.core.tools.mcp.registry.list_tools_http",
        new=AsyncMock(return_value=[descriptor]),
    ):
        await first.get_tools_async([server])

    second = MCPRegistry(descriptor_cache_root=tmp_path)
    with patch(
        "vibe.core.tools.mcp.registry.list_tools_http",
        new=AsyncMock(side_effect=AssertionError("cache hit performed MCP I/O")),
    ):
        await second.get_tools_async([server])

    assert second.descriptors_for(server.name) == (descriptor,)
