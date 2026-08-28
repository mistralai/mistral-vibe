from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum, auto
import logging
import os
from pathlib import Path
import re
import shlex
from string import Formatter
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from vibe.config_values import (
    THINKING_LEVELS as THINKING_LEVELS,
    SpeechOutputFormat,
    ThinkingLevel,
    TranscriptionEncoding,
)
from vibe.core.config._defaults import (
    DEFAULT_AUTO_COMPACT_THRESHOLD,
    DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
    DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
)
from vibe.core.paths import SESSION_LOG_DIR
from vibe.core.types import Backend

logger = logging.getLogger(__name__)


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
    # Background LLM-generated session titles (shown in --resume and the
    # terminal tab). Off falls back to the first-message preview.
    generate_titles: bool = True

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
    # Whether this provider reliably terminates streams with a finish reason.
    # When True, a stream that ends without one is treated as an incomplete
    # stream (and retried). Set to False for OpenAI-compatible endpoints that
    # do not emit a finish reason, to avoid spurious incomplete-stream errors.
    emits_finish_reason: bool = True
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


def normalize_mcp_server_name(value: str | None) -> str:
    if not value:
        return ""
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", value)
    return normalized.strip("_-")[:256]


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
        normalized = normalize_mcp_server_name(v)
        if not normalized:
            raise ValueError("MCP server name must contain letters or numbers")
        return normalized


_LEGACY_STATIC_AUTH_KEYS = (
    "headers",
    "api_key_env",
    "api_key_header",
    "api_key_format",
)
_ENV_VAR_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_HEADER_NAME_PATTERN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


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

    @field_validator("headers")
    @classmethod
    def _validate_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        normalized_names: set[str] = set()
        for name in headers:
            if not _HEADER_NAME_PATTERN.fullmatch(name):
                raise ValueError(f"Invalid HTTP header name {name!r}")
            normalized_name = name.lower()
            if normalized_name in normalized_names:
                raise ValueError(f"Duplicate HTTP header {name!r}")
            normalized_names.add(normalized_name)
        return headers

    @field_validator("api_key_env")
    @classmethod
    def _validate_api_key_env(cls, value: str) -> str:
        if value and not _ENV_VAR_PATTERN.fullmatch(value):
            raise ValueError("api_key_env must be a valid environment variable name")
        return value

    @field_validator("api_key_header")
    @classmethod
    def _validate_api_key_header(cls, value: str) -> str:
        if not _HEADER_NAME_PATTERN.fullmatch(value):
            raise ValueError("api_key_header must be a valid HTTP header name")
        return value

    @field_validator("api_key_format")
    @classmethod
    def _validate_api_key_format(cls, value: str) -> str:
        # Enumerate the real replacement fields rather than substring-matching
        # `{token}`: escaped braces (`{{token}}`) contain that substring but
        # yield no field, and a format spec (`{token:>5}`) is valid but omits it.
        try:
            fields = [
                name for _, name, _, _ in Formatter().parse(value) if name is not None
            ]
        except ValueError as exc:
            raise ValueError("api_key_format must be a valid format string") from exc
        if any(name != "token" for name in fields):
            raise ValueError("api_key_format may only reference the token placeholder")
        if "token" not in fields:
            raise ValueError("api_key_format must contain the `{token}` placeholder")
        return value

    @model_validator(mode="after")
    def _warn_inert_api_key_fields(self) -> MCPStaticAuth:
        if not self.api_key_env and (
            self.api_key_header != "Authorization"
            or self.api_key_format != "Bearer {token}"
        ):
            logger.warning(
                "MCP static auth sets api_key_header/api_key_format without "
                "api_key_env; these fields are ignored.",
                extra={
                    "api_key_header": self.api_key_header,
                    "api_key_format": self.api_key_format,
                },
            )
        return self

    def http_headers(self) -> dict[str, str]:
        hdrs = dict(self.headers)
        has_explicit_api_key_header = any(
            name.lower() == self.api_key_header.lower() for name in hdrs
        )
        if (
            not has_explicit_api_key_header
            and self.api_key_env
            and (token := os.getenv(self.api_key_env))
        ):
            hdrs[self.api_key_header] = self.api_key_format.format(token=token)
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


class ModelConfig(BaseModel):
    name: str
    provider: str
    alias: str
    display_name: str | None = None
    temperature: float = 0.2
    input_price: float = 0.0  # Price per million input tokens
    output_price: float = 0.0  # Price per million output tokens
    cached_input_price: float | None = (
        None  # Price per million cached input tokens; None bills them at input_price
    )
    thinking: ThinkingLevel = "off"
    supports_images: bool = False
    auto_compact_threshold: int = DEFAULT_AUTO_COMPACT_THRESHOLD
    _default_alias_to_name = model_validator(mode="before")(_default_alias_to_name)


def normalize_model_configs(value: Any) -> Any:
    """Read [[models]] lists or alias maps into the deep-mergeable internal map."""
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for alias, payload in value.items():
            normalized[alias] = _model_payload_with_alias(alias, payload)
        return normalized

    if isinstance(value, list):
        normalized: dict[str, Any] = {}
        for payload in value:
            alias = _model_alias_from_payload(payload)
            normalized[alias] = _model_payload_with_alias(alias, payload)
        return normalized

    return value


def serialize_model_configs(value: Any) -> Any:
    """Write the internal model map back as legacy [[models]] TOML entries.

    None-valued fields are dropped: TOML has no null and tomli_w rejects it.
    """
    normalized = normalize_model_configs(value)
    if not isinstance(normalized, Mapping):
        return normalized
    return [_serialize_model_entry(model) for model in normalized.values()]


def _serialize_model_entry(model: Any) -> Any:
    if isinstance(model, ModelConfig):
        return model.model_dump(exclude_none=True)
    if isinstance(model, Mapping):
        return {key: value for key, value in model.items() if value is not None}
    return model


def normalize_model_configs_with_defaults(value: Any, defaults: Any) -> Any:
    """Normalize models and fill sparse default-model overrides when required."""
    normalized = normalize_model_configs(value)
    if not isinstance(normalized, Mapping):
        return normalized

    default_models = normalize_model_configs(defaults)
    if not isinstance(default_models, Mapping):
        return normalized

    if not _needs_default_model_payloads(normalized, default_models):
        return normalized

    return merge_model_payloads(
        {
            alias: _model_payload_for_merge(model)
            for alias, model in default_models.items()
        },
        normalized,
    )


def _model_alias_from_payload(payload: Any) -> str:
    if isinstance(payload, ModelConfig):
        return payload.alias

    if not isinstance(payload, Mapping):
        raise ValueError("Model entries must be tables with an alias or name.")

    alias = payload.get("alias", payload.get("name"))
    if not isinstance(alias, str) or not alias:
        raise ValueError("Model entries must define a non-empty alias or name.")
    return alias


def _model_payload_with_alias(alias: Any, payload: Any) -> Any:
    if not isinstance(alias, str) or not alias:
        raise ValueError("Model aliases must be non-empty strings.")

    if isinstance(payload, ModelConfig):
        if payload.alias != alias:
            raise ValueError(
                f"Model key '{alias}' does not match model alias '{payload.alias}'."
            )
        return payload

    if not isinstance(payload, Mapping):
        return payload

    model_payload = dict(payload)
    existing_alias = model_payload.get("alias")
    if existing_alias is None:
        model_payload["alias"] = alias
        return model_payload

    if existing_alias != alias:
        raise ValueError(
            f"Model key '{alias}' does not match model alias '{existing_alias}'."
        )
    return model_payload


def _needs_default_model_payloads(
    models: Mapping[str, Any], defaults: Mapping[str, Any]
) -> bool:
    """Return whether a known default model is missing required identity fields."""
    for alias, payload in models.items():
        if alias not in defaults or not isinstance(payload, Mapping):
            continue
        if "name" not in payload or "provider" not in payload:
            return True
    return False


def _model_payload_for_merge(model: Any) -> dict[str, Any]:
    """Convert a default model entry into a plain mapping for deep merge."""
    if isinstance(model, ModelConfig):
        return model.model_dump(mode="json")
    if isinstance(model, Mapping):
        return dict(model)
    return {}


def merge_model_payloads(
    mapping: Mapping[str, Any], updating_mapping: Mapping[str, Any]
) -> dict[str, Any]:
    """Recursively merge model payload mappings without mutating either input."""
    merged = dict(mapping)
    for key, value in updating_mapping.items():
        existing = merged.get(key)
        if isinstance(value, Mapping) and isinstance(existing, Mapping):
            merged[key] = merge_model_payloads(existing, value)
            continue
        merged[key] = value
    return merged


class TranscribeModelConfig(BaseModel):
    name: str
    provider: str
    alias: str
    sample_rate: int = 16000
    encoding: TranscriptionEncoding = "pcm_s16le"
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


class OtelRedactionMode(StrEnum):
    DEFAULT = auto()
    NONE = auto()
    STRICT = auto()


class OtelSpanExporterConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    endpoint: str
    headers: dict[str, str] | None = None
