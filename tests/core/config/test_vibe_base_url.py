from __future__ import annotations

from typing import Any

import pytest

from tests.stubs.fake_raw_layer import FakeRawLayer
from vibe.core.config import DEFAULT_VIBE_BASE_URL
from vibe.core.config._vibe_base_url import (
    resolve_vibe_base_url,
    vibe_base_url_from_api_base,
)
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.layers.admin import AdminConfigLayer
from vibe.core.config.layers.default import DefaultConfigLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.vibe_schema import VibeConfigSchema

_PC_API_BASE = "https://api.adb6c3be0392.dc.mistral.ai/v1"
_PC_CHAT = "https://chat.adb6c3be0392.dc.mistral.ai"


@pytest.mark.parametrize(
    ("api_base", "expected"),
    [
        (_PC_API_BASE, _PC_CHAT),
        ("https://api.mistral.ai/v1", DEFAULT_VIBE_BASE_URL),
        ("https://api.mistral.ai:443/v1", DEFAULT_VIBE_BASE_URL),
        ("https://api.eu.mistral.ai/v1", None),
        ("https://api.us.mistral.ai:443/v1", None),
        ("https://api.example.com:8443/v1/", "https://chat.example.com:8443"),
        ("https://API.Example.com/v1", "https://chat.example.com"),
        ("https://mistral-api.acme.mistral.ninja/v1", None),
        ("http://api.example.com/v1", None),
        ("http://localhost:8080/v1", None),
        ("https://api./v1", None),
        ("https://user:secret@api.example.com/v1", None),
        ("https://api.example.com:abc/v1", "https://chat.example.com:abc"),
        ("https://[::1/v1", None),
        ("not a url", None),
        ("", None),
    ],
)
def test_vibe_base_url_from_api_base(api_base: str, expected: str | None) -> None:
    assert vibe_base_url_from_api_base(api_base) == expected


def _provider(api_base: str, *, backend: str = "mistral") -> dict[str, Any]:
    return {
        "name": "mistral",
        "backend": backend,
        "api_base": api_base,
        "api_key_env_var": "MISTRAL_API_KEY",
    }


async def _config(
    user: dict[str, Any], admin: dict[str, Any] | None = None
) -> VibeConfigSchema:
    layers: list[ConfigLayer[RawConfig]] = [
        DefaultConfigLayer(schema=VibeConfigSchema),
        FakeRawLayer(name="user-toml", data=user),
    ]
    if admin is not None:
        layers.append(AdminConfigLayer(data=admin))
    orchestrator = await ConfigOrchestrator[VibeConfigSchema].create(
        schema=VibeConfigSchema, layers=layers, default_layer_resolver=lambda: layers[1]
    )
    return orchestrator.config


@pytest.mark.asyncio
async def test_unset_vibe_base_url_follows_the_mistral_api_host() -> None:
    config = await _config({"providers": [_provider(_PC_API_BASE)]})

    assert config.vibe_base_url == DEFAULT_VIBE_BASE_URL
    assert resolve_vibe_base_url(config) == _PC_CHAT


@pytest.mark.asyncio
async def test_user_vibe_base_url_is_kept_even_when_it_is_the_default() -> None:
    config = await _config({
        "vibe_base_url": DEFAULT_VIBE_BASE_URL,
        "providers": [_provider(_PC_API_BASE)],
    })

    assert resolve_vibe_base_url(config) == DEFAULT_VIBE_BASE_URL


@pytest.mark.asyncio
async def test_user_custom_vibe_base_url_is_kept() -> None:
    config = await _config({
        "vibe_base_url": "https://chat.corp.example",
        "providers": [_provider(_PC_API_BASE)],
    })

    assert resolve_vibe_base_url(config) == "https://chat.corp.example"


@pytest.mark.asyncio
async def test_admin_vibe_base_url_wins_over_the_derived_one() -> None:
    config = await _config(
        {"providers": [_provider(_PC_API_BASE)]},
        admin={"vibe_base_url": "https://chat.admin.example"},
    )

    assert resolve_vibe_base_url(config) == "https://chat.admin.example"


@pytest.mark.asyncio
async def test_unrecognized_api_host_keeps_the_default() -> None:
    config = await _config({
        "providers": [_provider("https://mistral-api.acme.mistral.ninja/v1")]
    })

    assert resolve_vibe_base_url(config) == DEFAULT_VIBE_BASE_URL


@pytest.mark.asyncio
async def test_non_mistral_provider_does_not_derive() -> None:
    config = await _config({"providers": [_provider(_PC_API_BASE, backend="generic")]})

    assert resolve_vibe_base_url(config) == DEFAULT_VIBE_BASE_URL
