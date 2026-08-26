from __future__ import annotations

import pytest

from tests.conftest import build_test_vibe_app
from vibe import __version__
from vibe.cli.audio_request_metadata import build_audio_request_metadata


def test_build_audio_request_metadata_includes_session_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.cli.audio_request_metadata.get_platform_id", lambda: "test-os"
    )
    monkeypatch.setattr(
        "vibe.cli.audio_request_metadata.get_platform_version", lambda: "test-version"
    )

    assert build_audio_request_metadata(
        session_id="session-1", parent_session_id="parent-1"
    ) == {
        "os": "test-os",
        "os_version": "test-version",
        "version": __version__,
        "session_id": "session-1",
        "parent_session_id": "parent-1",
        "call_type": "secondary_call",
        "call_source": "vibe_code",
    }


def test_build_audio_request_metadata_omits_absent_optional_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.cli.audio_request_metadata.get_platform_version", lambda: None
    )

    metadata = build_audio_request_metadata(
        session_id="session-1", parent_session_id=None
    )

    assert "os_version" not in metadata
    assert "parent_session_id" not in metadata


@pytest.mark.asyncio
async def test_app_metadata_getter_reads_replaced_session_state() -> None:
    app = build_test_vibe_app()
    await app.prepare()
    metadata_getter = app._get_audio_request_metadata

    app.app_server.state.session = app.app_server.state.session.model_copy(
        update={"id": "replacement-session", "parent_session_id": "parent-session"}
    )

    metadata = metadata_getter()
    assert metadata["session_id"] == "replacement-session"
    assert metadata["parent_session_id"] == "parent-session"
