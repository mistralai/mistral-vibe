from __future__ import annotations

import sys
from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
import pytest

_in_mem_config: dict[str, Any] = {}


class InMemSettingsSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        return _in_mem_config.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return _in_mem_config


@pytest.fixture(autouse=True, scope="session")
def _patch_vibe_config() -> None:
    """Patch VibeConfig.settings_customise_sources to only use init_settings in tests.

    This ensures that even production code that creates VibeConfig instances
    will only use init_settings and ignore environment variables and config files.
    Runs once per test session before any tests execute.
    """
    from vibe.core.config import VibeConfig

    def patched_settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, InMemSettingsSource(settings_cls))

    VibeConfig.settings_customise_sources = classmethod(
        patched_settings_customise_sources
    )  # type: ignore[assignment]

    def dump_config(cls, config: dict[str, Any]) -> None:
        global _in_mem_config
        _in_mem_config = config

    VibeConfig.dump_config = classmethod(dump_config)  # type: ignore[assignment]

    def patched_load(cls, agent: str | None = None, **overrides: Any) -> Any:
        return cls(**overrides)

    VibeConfig.load = classmethod(patched_load)  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _reset_in_mem_config() -> None:
    """Reset in-memory config before each test to prevent test isolation issues.

    This ensures that each test starts with a clean configuration state,
    preventing race conditions and test interference when tests run in parallel
    or when VibeConfig.save_updates() modifies the shared _in_mem_config dict.
    """
    global _in_mem_config
    _in_mem_config = {}


@pytest.fixture(autouse=True)
def _mock_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "mock")


@pytest.fixture(autouse=True)
def _mock_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock platform to be Linux with /bin/sh shell for consistent test behavior.

    This ensures that platform-specific system prompt generation is consistent
    across all tests regardless of the actual platform running the tests.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("SHELL", "/bin/sh")
