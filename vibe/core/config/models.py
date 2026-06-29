from __future__ import annotations

from enum import StrEnum, auto
import os
from pathlib import Path
import re
import shlex
from typing import Annotated, Any, Literal, get_args

from mistralai.client.models import SpeechOutputFormat
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from vibe.core.config._defaults import (
    DEFAULT_AUTO_COMPACT_THRESHOLD,
    DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
    DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
)
from vibe.core.paths import SESSION_LOG_DIR
from vibe.core.types import Backend


class MissingAPIKeyError(RuntimeError):
    def __init__(self, env_key: str, provider_name: str) -> None:
        super().__init__(
            f"Missing {env_key} environment variable for {provider_name} provider"
        )
        self.env_key = env_key
        self.provider_name = provider_name


class ProjectContextConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    default_commit_count: int = 5
    timeout_seconds: float = 2.0


class ExperimentsConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    enable: bool = True
    api_host: str = "https://experiments.mistral.services/"
    client_key: str = "sdk-OE8yJgTXZY6tj"


class SessionLoggingConfig(BaseSettings):
    save_dir: str = ""
    session_prefix: str = "session"
    enabled: bool = True

    @field_validator("save_dir", mode="before")
    @classmethod
    def set_default_save_dir(cls, v: str) -> str:
        if not v:
            return str(SESSION_LOG_DIR.path)
        return v

    @field_validator("save_dir", mode="after")
    @classmethod
    def expand_save_dir(cls, v: str) -> str:
        return str(Path(v).expanduser().resolve())


class ProviderConfig(BaseModel):
    name: str
    api_base: str
    api_key_env_var: str = ""
    browser_auth_base_url: str | None = None
    browser_auth_api_base_url: str | None = None
    api_style: str = "openai"
    backend: Backend = Backend.GENERIC
    reasoning_field_name: str = "reasoning_content"
    project_id: str = ""
    region: str = ""
    extra_headers: dict[str, str] = Field(default_factory=dict)

    def _is_legacy_mistral_provider_without_backend(self) -> bool:
        return (
            self.name == "mistral"
            and self.backend == Backend.GENERIC
            and "backend" not in self.model_fields_set
        )

    def _uses_mistral_browser_sign_in_defaults(self) -> bool:
        return self.name == "mistral" and (
            self.backend == Backend.MISTRAL
            or self._is_legacy_mistral_provider_without_backend()
        )

    @model_validator(mode="after")
    def _apply_legacy_mistral_browser_auth_defaults(self) -> ProviderConfig:
        if not self._uses_mistral_browser_sign_in_defaults():
            return self

        if self.browser_auth_base_url is None:
            self.browser_auth_base_url = DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL
        if self.browser_auth_api_base_url is None:
            self.browser_auth_api_base_url = DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL
        return self

    @property
    def supports_browser_sign_in(self) -> bool:
        return (
            (self.backend == Backend.MISTRAL or self.name == "mistral")
            and bool(self.browser_auth_base_url)
            and bool(self.browser_auth_api_base_url)
        )


class TranscribeClient(StrEnum):
    MISTRAL = auto()


class TranscribeProviderConfig(BaseModel):
    name: str
    api_base: str = "wss://api.mistral.ai"
    api_key_env_var: str = ""
    client: TranscribeClient = TranscribeClient.MISTRAL


class _MCPBase(BaseModel):
    name: str = Field(description="Short alias used to prefix tool names")
    prompt: str | None = Field(
        default=None, description="Optional usage hint appended to tool descriptions"
    )
    startup_timeout_sec: float = Field(
        default=10.0,
        gt=0,
        description="Timeout in seconds for the server to start and initialize.",
    )
    tool_timeout_sec: float = Field(
        default=60.0, gt=0, description="Timeout in seconds for tool execution."
    )
    sampling_enabled: bool = Field(
        default=True,
        description="Allow this MCP server to request LLM completions via sampling/createMessage.",
    )
    disabled: bool = Field(
        default=False,
        description="Disable all tools from this MCP server. Tools are still discovered but hidden.",
    )
    disabled_tools: list[str] = Field(
        default_factory=list,
        description=(
            "Tool names (without the server prefix) to disable from this server. "
            "E.g. ['search', 'read'] to hide '{alias}_search' and '{alias}_read'."
        ),
    )

    @field_validator("name", mode="after")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", v)
        normalized = normalized.strip("_-")
        return normalized[:256]


_LEGACY_STATIC_AUTH_KEYS = (
    "headers",
    "api_key_env",
    "api_key_header",
    "api_key_format",
)


class MCPStaticAuth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["static"] = "static"
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Additional HTTP headers (e.g., Authorization or X-API-Key).",
    )
    api_key_env: str = Field(
        default="",
        description=(
            "Environment variable name containing an API token to send for HTTP transport."
        ),
    )
    api_key_header: str = Field(
        default="Authorization",
        description=(
            "HTTP header name to carry the token when 'api_key_env' is set (e.g., 'Authorization' or 'X-API-Key')."
        ),
    )
    api_key_format: str = Field(
        default="Bearer {token}",
        description=(
            "Format string for the header value when 'api_key_env' is set. Use '{token}' placeholder."
        ),
    )

    def http_headers(self) -> dict[str, str]:
        hdrs = dict(self.headers or {})
        env_var = (self.api_key_env or "").strip()
        if env_var and (token := os.getenv(env_var)):
            target = (self.api_key_header or "").strip() or "Authorization"
            if not any(h.lower() == target.lower() for h in hdrs):
                try:
                    value = (self.api_key_format or "{token}").format(token=token)
                except Exception:
                    value = token
                hdrs[target] = value
        return hdrs


class MCPOAuth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["oauth"]
    scopes: list[str] = Field(
        description="OAuth scopes to request. Pass an empty list to accept the AS default."
    )
    client_id: str | None = Field(
        default=None,
        min_length=1,
        description="Pre-registered OAuth public client_id (PKCE). Mutually exclusive with client_metadata_url.",
    )
    client_metadata_url: HttpUrl | None = Field(
        default=None,
        description="RFC 9728 client-metadata-document URL. Mutually exclusive with client_id.",
    )
    redirect_port: int = Field(
        default=47823,
        ge=1024,
        le=65535,
        description="Loopback port for the OAuth callback handler.",
    )

    @model_validator(mode="after")
    def _check_client_identity(self) -> MCPOAuth:
        if self.client_id and self.client_metadata_url:
            raise ValueError("client_id and client_metadata_url are mutually exclusive")
        return self


MCPAuth = Annotated[MCPStaticAuth | MCPOAuth, Field(discriminator="type")]


def _promote_legacy_auth(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    legacy_present = [k for k in _LEGACY_STATIC_AUTH_KEYS if k in data]
    if not legacy_present:
        return data
    if "auth" in data:
        raise ValueError(
            "cannot mix top-level "
            f"{', '.join(_LEGACY_STATIC_AUTH_KEYS)} with an explicit [auth] block; "
            'move legacy keys into [auth] (type = "static")'
        )
    data["auth"] = {"type": "static", **{k: data.pop(k) for k in legacy_present}}
    return data


class _MCPHttpFields(BaseModel):
    url: str = Field(description="Base URL of the MCP HTTP server")
    auth: MCPAuth = Field(default_factory=MCPStaticAuth)

    def http_headers(self) -> dict[str, str]:
        if isinstance(self.auth, MCPStaticAuth):
            return self.auth.http_headers()
        return {}


class MCPHttp(_MCPBase, _MCPHttpFields):
    transport: Literal["http"]

    _promote_legacy_auth = model_validator(mode="before")(_promote_legacy_auth)


class MCPStreamableHttp(_MCPBase, _MCPHttpFields):
    transport: Literal["streamable-http"]

    _promote_legacy_auth = model_validator(mode="before")(_promote_legacy_auth)


class MCPStdio(_MCPBase):
    transport: Literal["stdio"]
    command: str | list[str]
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables to set for the MCP server process.",
    )
    cwd: str | None = Field(
        default=None, description="Working directory for the MCP server process."
    )

    def argv(self) -> list[str]:
        base = (
            shlex.split(self.command)
            if isinstance(self.command, str)
            else list(self.command or [])
        )
        return [*base, *self.args] if self.args else base


MCPServer = Annotated[
    MCPHttp | MCPStreamableHttp | MCPStdio, Field(discriminator="transport")
]


class ConnectorConfig(BaseModel):
    name: str = Field(description="Normalized connector alias to match against.")
    disabled: bool = Field(
        default=False,
        description="Disable all tools from this connector. Tools are still discovered but hidden.",
    )
    disabled_tools: list[str] = Field(
        default_factory=list,
        description=(
            "Tool names (without the connector prefix) to disable. "
            "E.g. ['search'] to hide 'connector_{name}_search'."
        ),
    )


def _default_alias_to_name(data: Any) -> Any:
    if isinstance(data, dict):
        if "alias" not in data or data["alias"] is None:
            data["alias"] = data.get("name")
    return data


ThinkingLevel = Literal["off", "low", "medium", "high", "max"]
THINKING_LEVELS: list[str] = list(get_args(ThinkingLevel))


class ModelConfig(BaseModel):
    name: str
    provider: str
    alias: str
    temperature: float = 0.2
    input_price: float = 0.0  # Price per million input tokens
    output_price: float = 0.0  # Price per million output tokens
    thinking: ThinkingLevel = "off"
    supports_images: bool = False
    auto_compact_threshold: int = DEFAULT_AUTO_COMPACT_THRESHOLD
    _default_alias_to_name = model_validator(mode="before")(_default_alias_to_name)


class TranscribeModelConfig(BaseModel):
    name: str
    provider: str
    alias: str
    sample_rate: int = 16000
    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    language: str = "en"
    target_streaming_delay_ms: int = 500

    _default_alias_to_name = model_validator(mode="before")(_default_alias_to_name)


class TTSClient(StrEnum):
    MISTRAL = auto()


class TTSProviderConfig(BaseModel):
    name: str
    api_base: str = "https://api.mistral.ai"
    api_key_env_var: str = ""
    client: TTSClient = TTSClient.MISTRAL


class TTSModelConfig(BaseModel):
    name: str
    provider: str
    alias: str
    voice: str = "gb_jane_neutral"
    response_format: SpeechOutputFormat = "wav"

    _default_alias_to_name = model_validator(mode="before")(_default_alias_to_name)


class OtelSpanExporterConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    endpoint: str
    headers: dict[str, str] | None = None
