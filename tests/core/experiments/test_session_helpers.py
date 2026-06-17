from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.models import EvalResponse, ExperimentAttributes
from vibe.core.experiments.session import (
    hydrate_experiments_from_session,
    initialize_experiments,
)
from vibe.core.telemetry.types import TerminalEmulator


class _StubClient(RemoteEvalClient):
    def __init__(self, response: EvalResponse | None) -> None:
        self._response = response
        self.attributes: ExperimentAttributes | None = None

    async def evaluate(self, attributes: ExperimentAttributes) -> EvalResponse | None:
        self.attributes = attributes
        return self._response

    async def aclose(self) -> None:
        pass


def _make_config(
    *, enable_telemetry: bool = True, enable_experiments: bool = True
) -> Any:
    config = MagicMock()
    config.enable_telemetry = enable_telemetry
    config.experiments.enable = enable_experiments
    return config


@pytest.mark.asyncio
async def test_initialize_returns_false_when_telemetry_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persist = AsyncMock()
    session_logger = MagicMock()
    session_logger.persist_experiments = persist
    manager = ExperimentManager(client=_StubClient(None))

    result = await initialize_experiments(
        config=_make_config(enable_telemetry=False),
        manager=manager,
        session_logger=session_logger,
        entrypoint_metadata=None,
    )

    assert result is False
    persist.assert_not_called()


@pytest.mark.asyncio
async def test_initialize_returns_false_when_experiments_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `experiments.enable=False` must short-circuit even when telemetry is on
    # — it is the user's opt-out for being assigned to A/B tests without
    # disabling all telemetry.
    persist = AsyncMock()
    session_logger = MagicMock()
    session_logger.persist_experiments = persist
    manager = ExperimentManager(client=_StubClient(None))

    result = await initialize_experiments(
        config=_make_config(enable_experiments=False),
        manager=manager,
        session_logger=session_logger,
        entrypoint_metadata=None,
    )

    assert result is False
    persist.assert_not_called()


@pytest.mark.asyncio
async def test_initialize_returns_false_when_no_mistral_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: None,
    )
    persist = AsyncMock()
    session_logger = MagicMock()
    session_logger.persist_experiments = persist
    manager = ExperimentManager(client=_StubClient(None))

    result = await initialize_experiments(
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        entrypoint_metadata=None,
    )

    assert result is False
    persist.assert_not_called()


@pytest.mark.asyncio
async def test_initialize_returns_false_when_remote_eval_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: even if telemetry is enabled and a Mistral key is set,
    # a failed remote eval (returns None) leaves manager state empty —
    # the helper must NOT report success, so the caller skips the
    # unnecessary system prompt refresh.
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    monkeypatch.setattr(
        "vibe.core.experiments.session._build_attributes",
        lambda *_args, **_kwargs: ExperimentAttributes(
            userId="x", entrypoint="cli", agent_version="0", os="darwin"
        ),
    )
    persist = AsyncMock()
    session_logger = MagicMock()
    session_logger.persist_experiments = persist
    manager = ExperimentManager(client=_StubClient(None))

    result = await initialize_experiments(
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        entrypoint_metadata=None,
    )

    assert result is False
    persist.assert_not_called()


@pytest.mark.asyncio
async def test_initialize_returns_true_and_persists_when_remote_eval_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    monkeypatch.setattr(
        "vibe.core.experiments.session._build_attributes",
        lambda *_args, **_kwargs: ExperimentAttributes(
            userId="x", entrypoint="cli", agent_version="0", os="darwin"
        ),
    )
    persist = AsyncMock()
    session_logger = MagicMock()
    session_logger.persist_experiments = persist
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    manager = ExperimentManager(client=_StubClient(response))

    result = await initialize_experiments(
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        entrypoint_metadata=None,
    )

    assert result is True
    persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_initialize_uses_provided_terminal_emulator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    persist = AsyncMock()
    session_logger = MagicMock()
    session_logger.persist_experiments = persist
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    client = _StubClient(response)
    manager = ExperimentManager(client=client)

    result = await initialize_experiments(
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        entrypoint_metadata=None,
        terminal_emulator=TerminalEmulator.VSCODE,
    )

    assert result is True
    assert client.attributes is not None
    assert client.attributes.terminal_emulator is TerminalEmulator.VSCODE


@pytest.mark.asyncio
async def test_hydrate_returns_false_when_telemetry_disabled() -> None:
    session_logger = MagicMock()
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    session_logger.session_metadata.experiments = response
    manager = ExperimentManager(client=_StubClient(None))

    result = await hydrate_experiments_from_session(
        config=_make_config(enable_telemetry=False),
        manager=manager,
        session_logger=session_logger,
    )

    assert result is False
    assert manager.export_state() is None


@pytest.mark.asyncio
async def test_hydrate_returns_false_when_experiments_disabled() -> None:
    # Regression: without this gate, a user who flipped experiments.enable
    # to False between sessions would still resume into a hydrated variant.
    session_logger = MagicMock()
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    session_logger.session_metadata.experiments = response
    manager = ExperimentManager(client=_StubClient(None))

    result = await hydrate_experiments_from_session(
        config=_make_config(enable_experiments=False),
        manager=manager,
        session_logger=session_logger,
    )

    assert result is False
    assert manager.export_state() is None
