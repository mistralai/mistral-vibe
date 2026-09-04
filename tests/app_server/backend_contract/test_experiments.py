from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
from pathlib import Path
import tomllib
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
import tomli_w

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
)
from tests.constants import CONNECTORS_BOOTSTRAP_PATH, MISTRAL_BASE_URL
from vibe.app_server.protocol import (
    ClientCapabilities,
    ConfigReadParams,
    ConfigReadResponse,
    SessionOptions,
)
from vibe.app_server.session import AppServerSession
from vibe.core.config import build_default_orchestrator
from vibe.core.experiments._constants import build_eval_url
from vibe.core.experiments.cache import (
    load_cached_eval_response,
    store_cached_eval_response,
)
from vibe.core.experiments.models import EvalResponse

_CONSOLE_BASE_URL = "https://console.mistral.ai"
_WHOAMI_PATH = "/api/vibe/whoami"
_IDENTITY_PATH = "/v1/users/me"

_EVAL_API_HOST = "https://experiments.mistral.services/"
_EVAL_CLIENT_KEY = "sdk-test"

_ROUTED_ALIAS = "contract-routed"
_EXTRA_MODELS_VARIANT = json.dumps({
    "models": [
        {"name": "contract-routed-model", "provider": "mistral", "alias": _ROUTED_ALIAS}
    ]
})

# Only a forced rule (or a confirmed track) becomes a config override, so a bare
# ``defaultValue`` would resolve without changing anything.
_EVAL_PAYLOAD: dict[str, Any] = {
    "features": {
        "vibe_cli_extra_models": {
            "defaultValue": "{}",
            "rules": [{"force": _EXTRA_MODELS_VARIANT, "tracks": []}],
        }
    }
}

_SETTLE_SECONDS = 5.0
# Resolution is a background task with nothing to await, so a negative assertion
# has to give it a real chance to fire first.
_QUIET_SECONDS = 0.5


# The shared test config keeps telemetry off so a leaked key can never reach
# GrowthBook; a test about experiments turns it back on for itself.
def _configure_experiments(config_dir: Path, *, enable: bool = True) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["enable_telemetry"] = True
    config["experiments"] = {
        "enable": enable,
        "api_host": _EVAL_API_HOST,
        "client_key": _EVAL_CLIENT_KEY,
    }
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


@pytest.fixture
def growthbook_api(respx_mock: respx.MockRouter) -> respx.Route:
    respx_mock.get(f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}").mock(
        return_value=httpx.Response(200, json={"connectors": []})
    )
    respx_mock.get(f"{MISTRAL_BASE_URL}{_IDENTITY_PATH}").mock(
        return_value=httpx.Response(200, json={"id": "user-1"})
    )
    respx_mock.get(f"{_CONSOLE_BASE_URL}{_WHOAMI_PATH}").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan_type": "chat",
                "plan_name": "INDIVIDUAL",
                "customer_id": "cust-1",
                "prompt_switching_to_pro_plan": False,
            },
        )
    )
    eval_url = build_eval_url(_EVAL_API_HOST, _EVAL_CLIENT_KEY)
    assert eval_url is not None
    return respx_mock.post(eval_url).mock(
        return_value=httpx.Response(200, json=_EVAL_PAYLOAD)
    )


@pytest_asyncio.fixture
async def experiments_connection(
    experimental_harness: bool, config_dir: Path, growthbook_api: respx.Route
) -> AsyncIterator[BackendContractConnection]:
    _configure_experiments(config_dir)
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )
    try:
        yield connection
    finally:
        await connection.host.close()


async def _model_aliases(
    connection: BackendContractConnection, session: AppServerSession
) -> list[str]:
    # The catalog the *session* is serving, not the host's.
    response = ConfigReadResponse.model_validate(
        await connection.client.request(
            "config/read", ConfigReadParams(session_id=session.session_id)
        )
    )
    return [model.alias for model in response.config.models]


async def _await_alias(
    connection: BackendContractConnection, session: AppServerSession, alias: str
) -> list[str]:
    # Resolution runs in the background by design — it must never sit on the
    # session-open path — so there is no event to wait on.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _SETTLE_SECONDS
    aliases = await _model_aliases(connection, session)
    while alias not in aliases and loop.time() < deadline:
        await asyncio.sleep(0.05)
        aliases = await _model_aliases(connection, session)
    return aliases


async def _seed_the_eval_cache() -> None:
    orchestrator = await build_default_orchestrator()
    store_cached_eval_response(
        orchestrator.config, EvalResponse.model_validate(_EVAL_PAYLOAD)
    )


async def _read_cached_variants() -> EvalResponse | None:
    orchestrator = await build_default_orchestrator()
    return load_cached_eval_response(orchestrator.config)


async def _await_cached_variant() -> EvalResponse | None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _SETTLE_SECONDS
    cached = await _read_cached_variants()
    while cached is None and loop.time() < deadline:
        await asyncio.sleep(0.05)
        cached = await _read_cached_variants()
    return cached


@pytest.mark.asyncio
async def test_a_resolved_variant_is_cached_for_the_next_session(
    experiments_connection: BackendContractConnection, growthbook_api: respx.Route
) -> None:
    session = await experiments_connection.host.open_session()
    try:
        await _await_cached_variant()
    finally:
        await session.close()

    # The cache is what both backends share: Unified persists nothing per
    # session, so it is that backend's whole continuity mechanism.
    assert growthbook_api.call_count == 1
    cached = await _read_cached_variants()
    assert cached is not None
    assert cached.features["vibe_cli_extra_models"].rules[0].force == (
        _EXTRA_MODELS_VARIANT
    )


@pytest.mark.asyncio
async def test_unified_pushes_a_freshly_resolved_variant_into_the_live_session(
    experimental_harness: bool,
    experiments_connection: BackendContractConnection,
    growthbook_api: respx.Route,
) -> None:
    # Unified-only, and not because Unified is ahead: the legacy app server
    # gates live application on ``await_experiment_model``, which it computes as
    # ``session_id is None`` and then always calls with a freshly generated id,
    # so an uncached variant there governs from the *next* open.
    if not experimental_harness:
        pytest.skip("the legacy app server applies an uncached variant on next open")

    session = await experiments_connection.host.open_session()
    try:
        aliases = await _await_alias(experiments_connection, session, _ROUTED_ALIAS)
    finally:
        await session.close()

    assert _ROUTED_ALIAS in aliases
    assert growthbook_api.call_count == 1


@pytest.mark.asyncio
async def test_the_eval_request_names_the_backend_serving_the_session(
    experimental_harness: bool,
    experiments_connection: BackendContractConnection,
    growthbook_api: respx.Route,
) -> None:
    session = await experiments_connection.host.open_session()
    try:
        await _await_cached_variant()
    finally:
        await session.close()

    assert growthbook_api.calls
    attributes = json.loads(growthbook_api.calls.last.request.content)["attributes"]
    assert attributes["harness"] == ("unified" if experimental_harness else "legacy")
    assert attributes["userId"] == "user-1"


@pytest.mark.asyncio
async def test_no_eval_is_requested_when_the_ab_opt_out_is_set(
    experimental_harness: bool, config_dir: Path, growthbook_api: respx.Route
) -> None:
    _configure_experiments(config_dir, enable=False)
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )

    try:
        session = await connection.host.open_session()
        try:
            await asyncio.sleep(_QUIET_SECONDS)
            aliases = await _model_aliases(connection, session)
        finally:
            await session.close()
    finally:
        await connection.host.close()

    assert growthbook_api.call_count == 0
    assert _ROUTED_ALIAS not in aliases


@pytest.mark.asyncio
async def test_a_failed_eval_leaves_the_session_on_its_cached_variants(
    experimental_harness: bool, config_dir: Path, growthbook_api: respx.Route
) -> None:
    _configure_experiments(config_dir)
    await _seed_the_eval_cache()
    growthbook_api.mock(return_value=httpx.Response(500, text="down"))
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )

    try:
        session = await connection.host.open_session()
        try:
            await asyncio.sleep(_QUIET_SECONDS)
            aliases = await _model_aliases(connection, session)
        finally:
            await session.close()
    finally:
        await connection.host.close()

    # Fail-open means the last good assignment keeps governing, not that the
    # user silently falls back to control.
    assert growthbook_api.call_count == 1
    assert _ROUTED_ALIAS in aliases
