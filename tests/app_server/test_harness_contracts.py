from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from pydantic import ValidationError
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import create_test_app_server_session
from vibe.app_server.protocol import SessionSettingsUpdateParams
from vibe.core.config import SessionLoggingConfig, VibeConfigSchema
from vibe.core.session.session_loader import SessionLoader
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.core.types import LLMMessage, Role


def test_session_settings_require_a_strict_non_negative_update() -> None:
    with pytest.raises(ValidationError):
        SessionSettingsUpdateParams(session_id="session-1")
    with pytest.raises(ValidationError):
        SessionSettingsUpdateParams(session_id="session-1", max_turns=True)
    with pytest.raises(ValidationError):
        SessionSettingsUpdateParams(session_id="session-1", max_tokens=-1)

    assert SessionSettingsUpdateParams(session_id="session-1", max_turns=0)


@pytest.mark.asyncio
async def test_session_settings_update_uses_the_owned_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_loop = build_test_agent_loop()
    set_max_turns = Mock(wraps=agent_loop.set_max_turns)
    set_max_tokens = Mock(wraps=agent_loop.set_max_tokens)
    monkeypatch.setattr(agent_loop, "set_max_turns", set_max_turns)
    monkeypatch.setattr(agent_loop, "set_max_tokens", set_max_tokens)
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.resources.sessions.update_settings(max_turns=42, max_tokens=8192)
    finally:
        await session.close()

    set_max_turns.assert_called_once_with(42)
    set_max_tokens.assert_called_once_with(8192)


@pytest.mark.asyncio
async def test_config_schema_returns_the_canonical_versioned_schema() -> None:
    agent_loop = build_test_agent_loop()
    session = await create_test_app_server_session(agent_loop)

    try:
        response = await session.resources.config.read_schema()
    finally:
        await session.close()

    expected = VibeConfigSchema.model_json_schema(mode="serialization", by_alias=True)
    encoded = json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded, usedforsecurity=False).hexdigest()
    assert response.config_schema == expected
    assert response.config_schema_version == f"sha256:{digest}"


@pytest.mark.asyncio
async def test_workspace_trust_is_server_owned(
    tmp_working_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_working_directory / "AGENTS.md").write_text(
        "Trusted instructions", encoding="utf-8"
    )
    agent_loop = build_test_agent_loop()
    reload_config = AsyncMock()
    reload_runtime = AsyncMock()
    monkeypatch.setattr(agent_loop.config_orchestrator, "reload", reload_config)
    monkeypatch.setattr(agent_loop, "reload_with_initial_messages", reload_runtime)
    session = await create_test_app_server_session(agent_loop)

    try:
        status = await session.resources.workspace.trust_status(
            str(tmp_working_directory)
        )
        decided = await session.resources.workspace.decide_trust(
            "trust_cwd", cwd=str(tmp_working_directory)
        )
    finally:
        await session.close()

    assert status.status == "untrusted"
    assert status.details is not None
    assert status.details.detected_files == ["AGENTS.md"]
    assert status.details.repo_detected_files == []
    assert status.details.available_decisions == ["trust_cwd", "decline"]
    assert decided.status == "trusted"
    assert decided.details is None
    assert trusted_folders_manager.is_trusted(tmp_working_directory) is True
    reload_config.assert_awaited_once()
    reload_runtime.assert_awaited_once_with(reload_hooks=True)


@pytest.mark.asyncio
async def test_legacy_detached_fork_persists_to_the_session_store(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path / "sessions")
        )
    )
    agent_loop = build_test_agent_loop(config=config)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content="First", message_id="user-1"),
        LLMMessage(
            role=Role.assistant, content="First answer", message_id="assistant-1"
        ),
        LLMMessage(role=Role.user, content="Second", message_id="user-2"),
        LLMMessage(
            role=Role.assistant, content="Second answer", message_id="assistant-2"
        ),
    ])
    session = await create_test_app_server_session(agent_loop)

    try:
        response = await session.resources.sessions.fork("user-1", attach=False)
    finally:
        await session.close()

    assert (
        SessionLoader.find_session_by_id(
            response.state.session.id, config.session_logging
        )
        is not None
    )
