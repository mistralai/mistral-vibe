from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from vibe.core.config.layers.environment import EnvironmentLayer
from vibe.core.config.vibe_schema import VibeConfigSchema


@pytest.mark.asyncio
async def test_reads_env_vars() -> None:
    env = {
        "MISTRAL_API_KEY": "test-key",
        "VIBE_ACTIVE_MODEL": "mistral-large",
        "VIBE_VIM_KEYBINDINGS": "true",
        "VIBE_ENABLE_TELEMETRY": "0",
        "VIBE_UNKNOWN_VAR": "ignored",
        "VIBE_SESSION_LOGGING__ENABLED": "false",
        "VIBE_SESSION_LOGGING__SESSION_PREFIX": "mysession",
        "VIBE_API_TIMEOUT": ".12",
    }
    with patch.dict(os.environ, env, clear=True):
        layer = EnvironmentLayer(schema=VibeConfigSchema)
        data = await layer.load()

    assert data.model_dump() == {
        "active_model": "mistral-large",
        "vim_keybindings": True,
        "enable_telemetry": False,
        "session_logging": {"enabled": False, "session_prefix": "mysession"},
        "api_timeout": 0.12,
    }

    assert layer.name == "environment"


@pytest.mark.asyncio
async def test_no_vars_set_returns_empty() -> None:
    with patch.dict(os.environ, {}, clear=True):
        data = await EnvironmentLayer(schema=VibeConfigSchema).load()
    assert data.model_dump() == {}


@pytest.mark.asyncio
async def test_always_trusted() -> None:
    assert await EnvironmentLayer(schema=VibeConfigSchema).resolve_trust() is True
