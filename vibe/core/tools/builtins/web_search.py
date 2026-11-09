from __future__ import annotations

from pydantic import BaseModel, Field

from vibe.core.config import DEFAULT_MISTRAL_API_ENV_KEY, VibeConfigSchema
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, ToolPermission
from vibe.utils.api_keys import resolve_api_key


class WebSearchSource(BaseModel):
    title: str
    url: str


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, description="The search query")


class WebSearchResult(BaseModel):
    query: str
    answer: str
    sources: list[WebSearchSource] = Field(default_factory=list)


class WebSearchConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    timeout: int = Field(default=120, description="HTTP timeout in seconds.")
    model: str = Field(
        default="mistral-vibe-cli-with-tools",
        description="Mistral model to use for web search.",
    )


class WebSearch(
    BaseTool[WebSearchArgs, WebSearchResult, WebSearchConfig, BaseToolState]
):
    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        if config is None:
            return bool(resolve_api_key(DEFAULT_MISTRAL_API_ENV_KEY))

        provider = config.get_mistral_provider()
        if provider is None:
            return bool(resolve_api_key(DEFAULT_MISTRAL_API_ENV_KEY))

        return bool(resolve_api_key(cls._api_key_env_var(config)))

    @classmethod
    def _api_key_env_var(cls, config: VibeConfigSchema | None) -> str:
        if config is None:
            return DEFAULT_MISTRAL_API_ENV_KEY
        provider = config.get_mistral_provider()
        if provider is None:
            return DEFAULT_MISTRAL_API_ENV_KEY
        return provider.api_key_env_var or DEFAULT_MISTRAL_API_ENV_KEY
