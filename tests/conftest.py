from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# Mock tomli_w for tests environment
sys.modules["tomli_w"] = MagicMock()

# Mock aiofiles for tests environment
sys.modules["aiofiles"] = MagicMock()
sys.modules["aiofiles.os"] = MagicMock()

# Mock mistralai for tests environment
sys.modules["mistralai"] = MagicMock()
sys.modules["mistralai.models"] = MagicMock()
sys.modules["mistralai.client"] = MagicMock()

# Mock mistralai.Mistral to return an AsyncMock instance for its chat client
mock_mistral_client = AsyncMock()
mock_mistral_client.chat = AsyncMock()
mock_mistral_client.chat.complete_async = AsyncMock()
mock_mistral_client.chat.stream_async = AsyncMock()
sys.modules["mistralai"].Mistral = MagicMock(return_value=mock_mistral_client)

# Ensure SDKError inherits from Exception so it can be caught in try/except blocks
class MockSDKError(Exception):
    def __init__(self, message="", raw_response=None):
        super().__init__(message)
        self.raw_response = raw_response

sys.modules["mistralai"].SDKError = MockSDKError
sys.modules["mistralai.models"].SDKError = MockSDKError

from unittest.mock import AsyncMock, MagicMock
from vibe.core.types import LLMChunk
from pydantic import ValidationError
import json
import os
from tests.mock.utils import MOCK_DATA_ENV_VAR

# Mock tomli_w for tests environment
sys.modules["tomli_w"] = MagicMock()

# Mock aiofiles for tests environment
sys.modules["aiofiles"] = MagicMock()
sys.modules["aiofiles.os"] = MagicMock()

# Mock mistralai for tests environment
sys.modules["mistralai"] = MagicMock()
sys.modules["mistralai.models"] = MagicMock()
sys.modules["mistralai.client"] = MagicMock()

# Mock mistralai.Mistral to return an AsyncMock instance for its chat client
# This will be configured dynamically by the _configure_mistral_mocks fixture
mock_mistral_client = AsyncMock()
mock_mistral_client.chat = AsyncMock()
mock_mistral_client.chat.complete_async = AsyncMock()
mock_mistral_client.chat.stream_async = AsyncMock()
sys.modules["mistralai"].Mistral = MagicMock(return_value=mock_mistral_client)

# Ensure SDKError inherits from Exception so it can be caught in try/except blocks
class MockSDKError(Exception):
    def __init__(self, message="", raw_response=None):
        super().__init__(message)
        self.raw_response = raw_response

sys.modules["mistralai"].SDKError = MockSDKError
sys.modules["mistralai.models"].SDKError = MockSDKError

import enum

if not hasattr(enum, "StrEnum"):

    class StrEnum(str, enum.Enum):
        pass

    enum.StrEnum = StrEnum

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
def _configure_mistral_mocks() -> None:
    """Configure Mistral client mocks dynamically based on environment variables."""
    mock_data_str = os.environ.get(MOCK_DATA_ENV_VAR)
    if not mock_data_str:
        # No mock data set, use default unconfigured mocks
        return
        
    mock_data = json.loads(mock_data_str)
    try:
        chunks = [LLMChunk.model_validate(chunk) for chunk in mock_data]
    except ValidationError as e:
        raise ValueError(f"Invalid mock data: {e}") from e

    # Configure complete_async
    async def mock_complete_async(*args, **kwargs):
        if not chunks:
            raise RuntimeError("No chunks provided for complete_async mock")
        
        return_chunk = chunks[0]
        # Simulate a Pydantic object with choices, message, finish_reason, usage
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message = MagicMock()
        mock_response.choices[0].message.content = return_chunk.message.content
        mock_response.choices[0].message.tool_calls = return_chunk.message.tool_calls
        mock_response.choices[0].finish_reason = return_chunk.finish_reason
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = return_chunk.usage.prompt_tokens if return_chunk.usage else 0
        mock_response.usage.completion_tokens = return_chunk.usage.completion_tokens if return_chunk.usage else 0
        return mock_response
    
    mock_mistral_client.chat.complete_async.side_effect = mock_complete_async

    # Configure stream_async
    async def mock_stream_async(*args, **kwargs):
        for chunk in chunks:
            mock_stream_chunk = MagicMock()
            mock_stream_chunk.data = MagicMock()
            mock_stream_chunk.data.choices = [MagicMock()]
            mock_stream_chunk.data.choices[0].delta = MagicMock()
            mock_stream_chunk.data.choices[0].delta.content = chunk.message.content
            mock_stream_chunk.data.choices[0].delta.tool_calls = chunk.message.tool_calls
            mock_stream_chunk.data.choices[0].finish_reason = chunk.finish_reason
            mock_stream_chunk.data.usage = MagicMock()
            mock_stream_chunk.data.usage.prompt_tokens = chunk.usage.prompt_tokens if chunk.usage else 0
            mock_stream_chunk.data.usage.completion_tokens = chunk.usage.completion_tokens if chunk.usage else 0
            yield mock_stream_chunk
    
    mock_mistral_client.chat.stream_async.side_effect = mock_stream_async

