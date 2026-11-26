from __future__ import annotations

from pathlib import Path

import pytest

from vibe.app_server._mcp_discovery_service import MCPDiscoveryService
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess
from vibe.app_server.protocol import SessionOptions


@pytest.mark.asyncio
async def test_every_channel_of_the_process_shares_one_discovery_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: Two channels of one multiplexed process.
    *Do*: Build a session context on each.
    *Assert*: Both sessions discover through the process's one set of
      discovery flights, so a server one finds down the other leaves alone.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    channels = [HarnessProcess(services=process_services) for _ in range(2)]
    options = SessionOptions(cwd=str(tmp_path), trust_workspace=True)

    # Do
    contexts = [
        await channel.build_unified_session_context(options) for channel in channels
    ]

    # Assert
    first, second = (context.mcp_discovery_flights for context in contexts)
    assert first is second
    assert first is process_services.mcp_discovery.flights()


@pytest.mark.asyncio
async def test_the_process_flights_back_off_a_failed_server() -> None:
    """*Prepare*: The service, as an entry point builds it.
    *Do*: Discover a server that fails, then discover it again.
    *Assert*: The second is not attempted: the first failure is raised again.
    """
    # Prepare
    from mistralai_vibe_local_harness.vibe._mcp_models import (
        MCPDescriptorCacheKey,
        MCPRemoteToolDescriptor,
        MCPRuntimeFailure,
    )

    service = MCPDiscoveryService()
    flights = service.flights()
    key = (
        MCPDescriptorCacheKey(
            format_version=1,
            naming_version="naming",
            server_fingerprint="fingerprint",
            authorization_descriptor_revision="revision",
        ),
        "credential",
    )
    attempts: list[str] = []

    async def fail() -> tuple[MCPRemoteToolDescriptor, ...]:
        attempts.append("down")
        raise MCPRuntimeFailure("mcp_transport_failed", "down")

    with pytest.raises(MCPRuntimeFailure):
        await flights.join(key, fail, closed=lambda: False)

    # Do
    with pytest.raises(MCPRuntimeFailure) as again:
        await flights.join(key, fail, closed=lambda: False)
    await service.close()

    # Assert
    assert attempts == ["down"]
    assert again.value.code == "mcp_transport_failed"
