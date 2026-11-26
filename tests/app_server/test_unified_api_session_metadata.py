"""The Vibe-owned ``meta.json`` sidecar of a session on the Session API path."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.app_server._session_api_harness import api_adapter
from vibe.app_server._process_services import ProcessServices


@pytest.mark.asyncio
async def test_the_session_metadata_records_the_session_api_path(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    """*Prepare*: A real adapter on the Session API path.
    *Do*: Persist its session metadata.
    *Assert*: The metadata names the Session API as the runtime path.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        # Do
        metadata = await harness.adapter._persist_session_metadata()

    # Assert
    assert metadata.runtime_path == "session_api"
