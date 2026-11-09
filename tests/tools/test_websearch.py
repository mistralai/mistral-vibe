from __future__ import annotations

from typing import TYPE_CHECKING

from tests.conftest import build_test_vibe_config
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.tools.builtins.web_search import WebSearch
from vibe.core.tools.manager import ToolManager
from vibe.core.types import Backend

if TYPE_CHECKING:
    pass


def _mistral_provider(
    api_key_env_var: str = "MISTRAL_API_KEY",
    api_base: str = "https://on-prem.example.com/v1",
) -> ProviderConfig:
    return ProviderConfig(
        name="mistral",
        api_base=api_base,
        api_key_env_var=api_key_env_var,
        backend=Backend.MISTRAL,
    )


def _llamacpp_provider() -> ProviderConfig:
    return ProviderConfig(
        name="llamacpp", api_base="http://127.0.0.1:8080/v1", backend=Backend.GENERIC
    )


def _llamacpp_model() -> ModelConfig:
    return ModelConfig(name="devstral", provider="llamacpp", alias="local-llamacpp")


def test_is_available_with_key(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "key")
    assert WebSearch.is_available() is True


def test_is_available_without_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    assert WebSearch.is_available() is False


def test_is_available_uses_mistral_provider_api_key_env_var(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "fallback-key")
    monkeypatch.setenv("TEST_API_KEY", "provider-key")
    config = build_test_vibe_config(providers=[_mistral_provider("TEST_API_KEY")])
    monkeypatch.delenv("TEST_API_KEY", raising=False)

    assert WebSearch.is_available(config) is False

    monkeypatch.setenv("TEST_API_KEY", "provider-key")
    assert WebSearch.is_available(config) is True


def test_is_available_uses_non_active_mistral_provider(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "provider-key")
    config = build_test_vibe_config(
        active_model="local-llamacpp",
        models=[_llamacpp_model()],
        providers=[_llamacpp_provider(), _mistral_provider("TEST_API_KEY")],
    )
    monkeypatch.delenv("TEST_API_KEY", raising=False)

    assert WebSearch.is_available(config) is False

    monkeypatch.setenv("TEST_API_KEY", "provider-key")
    assert WebSearch.is_available(config) is True


def test_is_available_falls_back_to_default_api_key_env_var_without_mistral_provider(
    monkeypatch,
):
    monkeypatch.setenv("MISTRAL_API_KEY", "fallback-key")
    config = build_test_vibe_config(
        active_model="local-llamacpp",
        models=[_llamacpp_model()],
        providers=[_llamacpp_provider()],
    )

    assert WebSearch.is_available(config) is True

    monkeypatch.delenv("MISTRAL_API_KEY")

    assert WebSearch.is_available(config) is False


def test_is_available_falls_back_to_default_api_key_env_var_when_provider_env_var_empty(
    monkeypatch,
):
    monkeypatch.setenv("MISTRAL_API_KEY", "fallback-key")
    config = build_test_vibe_config(providers=[_mistral_provider("")])

    assert WebSearch.is_available(config) is True

    monkeypatch.delenv("MISTRAL_API_KEY")

    assert WebSearch.is_available(config) is False


def test_tool_manager_websearch_availability_uses_provider_api_key_env_var(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setenv("TEST_API_KEY", "provider-key")
    config = build_test_vibe_config(providers=[_mistral_provider("TEST_API_KEY")])
    manager = ToolManager(lambda: config)

    assert "web_search" in manager.available_tools

    monkeypatch.delenv("TEST_API_KEY")
    assert "web_search" not in manager.available_tools


def test_tool_manager_websearch_availability_falls_back_without_mistral_provider(
    monkeypatch,
):
    monkeypatch.setenv("MISTRAL_API_KEY", "fallback-key")
    config = build_test_vibe_config(
        active_model="local-llamacpp",
        models=[_llamacpp_model()],
        providers=[_llamacpp_provider()],
    )
    manager = ToolManager(lambda: config)

    assert "web_search" in manager.available_tools

    monkeypatch.delenv("MISTRAL_API_KEY")
    assert "web_search" not in manager.available_tools
