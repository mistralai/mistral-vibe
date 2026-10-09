"""Production wiring of the model catalog: root blueprint and session/start.

The loop-level behavior is covered by tests/core/agent_loop/test_model_catalog.py;
these tests prove the real construction paths set the flags and start the
fetch — the layer where a dead condition would otherwise go untested.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import create_test_app_server_session
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
import vibe.app_server._runtime as runtime_module
from vibe.app_server._runtime import AgentRuntimeFactory, _RootRuntimeBlueprint
from vibe.app_server.protocol import ClientCapabilities, ClientInfo, SessionOptions
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.hooks.models import HookConfigResult
from vibe.core.model_catalog import ModelCatalogResponse, parse_model_catalog_response
from vibe.utils.cache_store import FileSystemCacheStore


def _catalog(model_id: str = "mistral-medium-3.5") -> ModelCatalogResponse:
    parsed = parse_model_catalog_response({
        "models": [{"id": model_id, "label": model_id, "recommended": True}]
    })
    assert parsed is not None
    return parsed


def _blueprint(config: Any, cache_dir: Path) -> _RootRuntimeBlueprint:
    return _RootRuntimeBlueprint(
        config_orchestrator=FakeConfigOrchestrator(config),
        harness_files=get_harness_files_manager(),
        options=SessionOptions(),
        client_info=ClientInfo(name="test", version="0.0.0"),
        client_capabilities=ClientCapabilities(),
        hook_config_result=HookConfigResult(hooks=[], issues=[]),
        cache_store=FileSystemCacheStore(cache_dir / "cache.toml"),
    )


def test_fresh_session_with_no_cache_defers_the_first_turn(tmp_path: Path) -> None:
    config = build_test_vibe_config(experimental_enable_model_catalog=True)
    blueprint = _blueprint(config, tmp_path)

    loop = blueprint.build(session_id="fresh-1", fresh_session=True)

    assert loop._await_model_catalog is True
    # The experiments deferral keeps its own pre-existing formula; the
    # catalog flag must not change experiments behavior while disabled.
    assert loop._await_experiment_model is False


def test_resumed_session_never_defers(tmp_path: Path) -> None:
    config = build_test_vibe_config(experimental_enable_model_catalog=True)
    blueprint = _blueprint(config, tmp_path)

    loop = blueprint.build(session_id="resumed-1")

    assert loop._await_model_catalog is False
    assert loop._await_experiment_model is False


def test_flag_disabled_never_defers_and_never_reads_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config()  # flag defaults to False
    load_cache = Mock(return_value=_catalog())
    monkeypatch.setattr(runtime_module, "load_cached_model_catalog", load_cache)
    blueprint = _blueprint(config, tmp_path)

    loop = blueprint.build(session_id="fresh-1", fresh_session=True)

    assert loop._await_model_catalog is False
    assert loop.model_catalog is None
    load_cache.assert_not_called()


def test_cached_catalog_applies_and_skips_the_deferral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config(experimental_enable_model_catalog=True)
    cached = _catalog()
    monkeypatch.setattr(
        runtime_module, "load_cached_model_catalog", lambda _config: cached
    )
    blueprint = _blueprint(config, tmp_path)

    loop = blueprint.build(session_id="fresh-1", fresh_session=True)

    assert loop.model_catalog is cached
    assert loop._await_model_catalog is False


@pytest.mark.asyncio
async def test_session_start_starts_the_catalog_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_vibe_config(experimental_enable_model_catalog=True)
    agent_loop = build_test_agent_loop(config=config)
    fetch = Mock()
    monkeypatch.setattr(agent_loop, "start_fetch_model_catalog", fetch)

    session = await create_test_app_server_session(agent_loop)
    try:
        fetch.assert_called_once_with()
    finally:
        await session.close()


def test_fork_and_subagent_loops_carry_the_parent_catalog() -> None:
    config = build_test_vibe_config(experimental_enable_model_catalog=True)
    source = build_test_agent_loop(
        config=config, model_catalog_state=_catalog("parent-model")
    )

    forked = AgentRuntimeFactory._create_like(source, agent_name="accept-edits")
    subagent = AgentRuntimeFactory._create_like(
        source, agent_name="ask", is_subagent=True
    )

    assert forked.model_catalog is source.model_catalog
    assert subagent.model_catalog is source.model_catalog
