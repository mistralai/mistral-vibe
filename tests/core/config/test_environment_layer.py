from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from vibe.core.config import build_default_orchestrator
from vibe.core.config.layers.environment import EnvironmentLayer
from vibe.core.config.vibe_schema import VibeConfigSchema


@pytest.mark.asyncio
async def test_reads_env_vars() -> None:
    env = {
        "MISTRAL_API_KEY": "test-key",
        "VIBE_ACTIVE_MODEL": "mistral-large",
        "VIBE_DISABLE_WELCOME_BANNER_ANIMATION": "true",
        "VIBE_ENABLE_TELEMETRY": "0",
        "VIBE_UNKNOWN_VAR": "ignored",
        "VIBE_SESSION_LOGGING__ENABLED": "false",
        "VIBE_SESSION_LOGGING__SESSION_PREFIX": "mysession",
        "VIBE_SESSION_LOGGING__GENERATE_TITLES": "false",
        "VIBE_API_TIMEOUT": ".12",
    }
    with patch.dict(os.environ, env, clear=True):
        layer = EnvironmentLayer(schema=VibeConfigSchema)
        data = await layer.load()

    assert data.model_dump() == {
        "active_model": "mistral-large",
        "disable_welcome_banner_animation": True,
        "enable_telemetry": False,
        "session_logging": {
            "enabled": False,
            "session_prefix": "mysession",
            "generate_titles": False,
        },
        "api_timeout": 0.12,
    }

    assert layer.name == "environment"


@pytest.mark.asyncio
async def test_no_vars_set_returns_empty() -> None:
    with patch.dict(os.environ, {}, clear=True):
        data = await EnvironmentLayer(schema=VibeConfigSchema).load()
    assert data.model_dump() == {}


@pytest.mark.asyncio
async def test_fingerprint_changes_when_env_changes() -> None:
    with patch.dict(os.environ, {"VIBE_ACTIVE_MODEL": "first-model"}, clear=True):
        layer = EnvironmentLayer(schema=VibeConfigSchema)
        data1 = await layer.load()
        fp1 = layer.fingerprint

        os.environ["VIBE_ACTIVE_MODEL"] = "second-model"
        data2 = await layer.load(force=True)
        fp2 = layer.fingerprint

    assert data1.model_dump() == {"active_model": "first-model"}
    assert data2.model_dump() == {"active_model": "second-model"}
    assert isinstance(fp1, str)
    assert fp1
    assert isinstance(fp2, str)
    assert fp2
    assert fp1 != fp2


@pytest.mark.asyncio
async def test_always_trusted() -> None:
    assert await EnvironmentLayer(schema=VibeConfigSchema).resolve_trust() is True


@pytest.mark.asyncio
async def test_unparseable_vars_are_ignored_and_the_others_still_apply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    env = {
        "VIBE_MODELS": "mistral-large-latest,mistral-medium-latest",
        "VIBE_API_TIMEOUT": "soon",
        "VIBE_ACTIVE_MODEL": "mistral-large",
        "VIBE_SESSION_LOGGING__ENABLED": "false",
    }
    with patch.dict(os.environ, env, clear=True), caplog.at_level("WARNING"):
        data = await EnvironmentLayer(schema=VibeConfigSchema).load()

    assert data.model_dump() == {
        "active_model": "mistral-large",
        "session_logging": {"enabled": False},
    }
    assert "VIBE_MODELS or VIBE_MODELS__*" in caplog.text
    assert "VIBE_API_TIMEOUT or VIBE_API_TIMEOUT__*" in caplog.text
    assert "mistral-large-latest" not in caplog.text


@pytest.mark.asyncio
async def test_an_ignored_var_says_which_fields_are_wrong_without_its_value() -> None:
    env = {"VIBE_MODELS": json.dumps([{"alias": "glm-5-3", "thinking": "max"}])}
    with patch.dict(os.environ, env, clear=True):
        layer = EnvironmentLayer(schema=VibeConfigSchema)
        await layer.load()

    assert layer.load_warnings == (
        "Ignoring unparseable VIBE_MODELS or VIBE_MODELS__* environment value:"
        " models.glm-5-3.name: Field required;"
        " models.glm-5-3.provider: Field required",
    )


@pytest.mark.asyncio
async def test_an_ignored_var_never_quotes_its_value() -> None:
    env = {"VIBE_MCP_SERVERS": json.dumps([{"name": "x", "transport": "s3cr3t"}])}
    with patch.dict(os.environ, env, clear=True):
        layer = EnvironmentLayer(schema=VibeConfigSchema)
        await layer.load()

    assert layer.load_warnings == (
        "Ignoring unparseable VIBE_MCP_SERVERS or VIBE_MCP_SERVERS__* environment"
        " value: mcp_servers.0: union_tag_invalid",
    )


@pytest.mark.asyncio
async def test_an_ignored_var_is_a_config_validation_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBE_API_TIMEOUT", "soon")

    config = (await build_default_orchestrator()).config

    assert any(
        warning.startswith("Ignoring unparseable VIBE_API_TIMEOUT")
        for warning in config.validation_warnings
    )


@pytest.mark.asyncio
async def test_parseable_vars_leave_no_warning() -> None:
    with patch.dict(os.environ, {"VIBE_ACTIVE_MODEL": "mistral-large"}, clear=True):
        layer = EnvironmentLayer(schema=VibeConfigSchema)
        await layer.load()

    assert layer.load_warnings == ()


@pytest.mark.asyncio
async def test_an_unparseable_nested_var_drops_only_its_group() -> None:
    env = {
        "VIBE_SESSION_LOGGING__ENABLED": "maybe",
        "VIBE_SESSION_LOGGING__SESSION_PREFIX": "mysession",
        "VIBE_ACTIVE_MODEL": "mistral-large",
    }
    with patch.dict(os.environ, env, clear=True):
        data = await EnvironmentLayer(schema=VibeConfigSchema).load()

    assert data.model_dump() == {"active_model": "mistral-large"}
