from __future__ import annotations

from collections.abc import MutableMapping
from enum import StrEnum, auto
import os
from pathlib import Path
import re
import shlex
import tomllib
from typing import Annotated, Any, Literal
from urllib.parse import urljoin

from dotenv import dotenv_values
from mistralai.client.models import SpeechOutputFormat
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    DEFAULT_TRACES_EXPORT_PATH,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_core import to_jsonable_python
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources.base import deep_update
import tomli_w

from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.logger import logger
from vibe.core.paths import GLOBAL_ENV_FILE, SESSION_LOG_DIR
from vibe.core.prompts import SystemPrompt
from vibe.core.types import Backend
from vibe.core.utils import get_server_url_from_api_base
from vibe.core.utils.io import read_safe


def load_dotenv_values(
    env_path: Path = GLOBAL_ENV_FILE.path,
    environ: MutableMapping[str, str] = os.environ,
) -> None:
    # We allow FIFO path to support some environment management solutions (e.g. https://developer.1password.com/docs/environments/local-env-file/)
    if not env_path.is_file() and not env_path.is_fifo():
        return

    env_vars = dotenv_values(env_path)
    for key, value in env_vars.items():
        if not value:
            continue
        environ.update({key: value})


class MissingAPIKeyError(RuntimeError):
    def __init__(self, env_key: str, provider_name: str) -> None:
        super().__init__(
            f"Missing {env_key} environment variable for {provider_name} provider"
        )
        self.env_key = env_key
        self.provider_name = provider_name


class MissingPromptFileError(RuntimeError):
    def __init__(self, system_prompt_id: str, *prompt_dirs: str) -> None:
        dirs_str = " or ".join(prompt_dirs) if prompt_dirs else "<no prompt dirs>"
        super().__init__(
            f"Invalid system_prompt_id value: '{system_prompt_id}'. "
            f"Must be one of the available prompts ({', '.join(f'{p.name.lower()}' for p in SystemPrompt)}), "
            f"or correspond to a .md file in {dirs_str}"
        )
        self.system_prompt_id = system_prompt_id


class TomlFileSettingsSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self.toml_data = self._load_toml()

    def _load_toml(self) -> dict[str, Any]:
        file = get_harness_files_manager().config_file
        if file is None:
            return {}
        try:
            with file.open("rb") as f:
                return tomllib.load(f)
        except FileNotFoundError:
            return {}
        except tomllib.TOMLDecodeError as e:
            raise RuntimeError(f"Invalid TOML in {file}: {e}") from e
        except OSError as e:
            raise RuntimeError(f"Cannot read {file}: {e}") from e

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        return self.toml_data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return self.toml_data


def _remove_none_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: cleaned_value
            for key, item in value.items()
            if (cleaned_value := _remove_none_values(item)) is not None
        }
    if isinstance(value, list):
        return [
            cleaned_item
            for item in value
            if (cleaned_item := _remove_none_values(item)) is not None
        ]
    return value


def _to_toml_document(value: Any) -> dict[str, Any]:
    jsonable = to_jsonable_python(value, fallback=str)
    if not isinstance(jsonable, dict):
        return {}
    return _remove_none_values(jsonable)


class ProjectContextConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    default_commit_count: int = 5
    timeout_seconds: float = 2.0


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


DEFAULT_MISTRAL_API_ENV_KEY = "MISTRAL_API_KEY"
DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL = "https://console.mistral.ai"
DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL = "https://console.mistral.ai/api"


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

    @field_validator("name", mode="after")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", v)
        normalized = normalized.strip("_-")
        return normalized[:256]


class _MCPHttpFields(BaseModel):
    url: str = Field(description="Base URL of the MCP HTTP server")
    headers: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Additional HTTP headers when using 'http' transport (e.g., Authorization or X-API-Key)."
        ),
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


class MCPHttp(_MCPBase, _MCPHttpFields):
    transport: Literal["http"]


class MCPStreamableHttp(_MCPBase, _MCPHttpFields):
    transport: Literal["streamable-http"]


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


def _default_alias_to_name(data: Any) -> Any:
    if isinstance(data, dict):
        if "alias" not in data or data["alias"] is None:
            data["alias"] = data.get("name")
    return data


class ModelConfig(BaseModel):
    name: str
    provider: str
    alias: str
    temperature: float = 0.2
    input_price: float = 0.0  # Price per million input tokens
    output_price: float = 0.0  # Price per million output tokens
    thinking: Literal["off", "low", "medium", "high", "max"] = "off"
    auto_compact_threshold: int = 200_000

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


MISTRAL_OTEL_PATH = "/telemetry"
_DEFAULT_MISTRAL_SERVER_URL = "https://api.mistral.ai"

DEFAULT_PROVIDERS = [
    ProviderConfig(
        name="mistral",
        api_base=f"{_DEFAULT_MISTRAL_SERVER_URL}/v1",
        api_key_env_var=DEFAULT_MISTRAL_API_ENV_KEY,
        browser_auth_base_url=DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
        browser_auth_api_base_url=DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
        backend=Backend.MISTRAL,
    ),
    ProviderConfig(
        name="llamacpp",
        api_base="http://127.0.0.1:8080/v1",
        api_key_env_var="",  # NOTE: if you wish to use --api-key in llama-server, change this value
    ),
]

DEFAULT_MODELS = [
    ModelConfig(
        name="mistral-vibe-cli-latest",
        provider="mistral",
        alias="devstral-2",
        input_price=0.4,
        output_price=2.0,
    ),
    ModelConfig(
        name="devstral-small-latest",
        provider="mistral",
        alias="devstral-small",
        input_price=0.1,
        output_price=0.3,
    ),
    ModelConfig(
        name="devstral",
        provider="llamacpp",
        alias="local",
        input_price=0.0,
        output_price=0.0,
    ),
]

DEFAULT_ACTIVE_MODEL = DEFAULT_MODELS[0].alias

DEFAULT_TRANSCRIBE_PROVIDERS = [
    TranscribeProviderConfig(
        name="mistral",
        api_base="wss://api.mistral.ai",
        api_key_env_var=DEFAULT_MISTRAL_API_ENV_KEY,
    )
]

DEFAULT_TRANSCRIBE_MODELS = [
    TranscribeModelConfig(
        name="voxtral-mini-transcribe-realtime-2602",
        provider="mistral",
        alias="voxtral-realtime",
    )
]

DEFAULT_TTS_PROVIDERS = [
    TTSProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai",
        api_key_env_var=DEFAULT_MISTRAL_API_ENV_KEY,
    )
]

DEFAULT_TTS_MODELS = [
    TTSModelConfig(
        name="voxtral-mini-tts-latest", provider="mistral", alias="voxtral-tts"
    )
]


class VibeConfig(BaseSettings):
    active_model: str = DEFAULT_ACTIVE_MODEL
    vim_keybindings: bool = False
    disable_welcome_banner_animation: bool = False
    autocopy_to_clipboard: bool = True
    file_watcher_for_autocomplete: bool = False
    displayed_workdir: str = ""
    context_warnings: bool = False
    voice_mode_enabled: bool = False
    narrator_enabled: bool = False
    active_transcribe_model: str = "voxtral-realtime"
    active_tts_model: str = "voxtral-tts"
    auto_approve: bool = False
    enable_telemetry: bool = True
    system_prompt_id: str = "cli"
    include_commit_signature: bool = True
    include_model_info: bool = True
    include_project_context: bool = True
    include_prompt_detail: bool = True
    enable_update_checks: bool = True
    enable_auto_update: bool = True
    enable_notifications: bool = True
    api_timeout: float = 720.0
    auto_compact_threshold: int = 200_000

    nuage_enabled: bool = Field(default=False, exclude=True)
    nuage_base_url: str = Field(default="https://api.mistral.ai", exclude=True)
    nuage_workflow_id: str = Field(default="__shared-nuage-workflow", exclude=True)
    nuage_task_queue: str | None = Field(default="shared-vibe-nuage", exclude=True)
    nuage_api_key_env_var: str = Field(default="MISTRAL_API_KEY", exclude=True)
    nuage_project_name: str = Field(default="Vibe", exclude=True)

    # TODO(otel): remove exclude=True once the feature is publicly available
    enable_otel: bool = Field(default=False, exclude=True)
    otel_endpoint: str = Field(default="", exclude=True)

    providers: list[ProviderConfig] = Field(
        default_factory=lambda: list(DEFAULT_PROVIDERS)
    )
    models: list[ModelConfig] = Field(default_factory=lambda: list(DEFAULT_MODELS))
    compaction_model: ModelConfig | None = None

    transcribe_providers: list[TranscribeProviderConfig] = Field(
        default_factory=lambda: list(DEFAULT_TRANSCRIBE_PROVIDERS)
    )
    transcribe_models: list[TranscribeModelConfig] = Field(
        default_factory=lambda: list(DEFAULT_TRANSCRIBE_MODELS)
    )

    tts_providers: list[TTSProviderConfig] = Field(
        default_factory=lambda: list(DEFAULT_TTS_PROVIDERS)
    )
    tts_models: list[TTSModelConfig] = Field(
        default_factory=lambda: list(DEFAULT_TTS_MODELS)
    )

    project_context: ProjectContextConfig = Field(default_factory=ProjectContextConfig)
    session_logging: SessionLoggingConfig = Field(default_factory=SessionLoggingConfig)
    tools: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tool_paths: list[Path] = Field(
        default_factory=list,
        description=(
            "Additional directories or files to explore for custom tools. "
            "Paths may be absolute or relative to the current working directory. "
            "Directories are shallow-searched for tool definition files, "
            "while files are loaded directly if valid."
        ),
    )

    mcp_servers: list[MCPServer] = Field(
        default_factory=list, description="Preferred MCP server configuration entries."
    )

    enabled_tools: list[str] = Field(
        default_factory=list,
        description=(
            "An explicit list of tool names/patterns to enable. If set, only these"
            " tools will be active. Supports glob patterns (e.g., 'serena_*') and"
            " regex with 're:' prefix (e.g., 're:^serena_.*')."
        ),
    )
    disabled_tools: list[str] = Field(
        default_factory=list,
        description=(
            "A list of tool names/patterns to disable. Ignored if 'enabled_tools'"
            " is set. Supports glob patterns and regex with 're:' prefix."
        ),
    )
    agent_paths: list[Path] = Field(
        default_factory=list,
        description=(
            "Additional directories to search for custom agent profiles. "
            "Each path may be absolute or relative to the current working directory."
        ),
    )
    enabled_agents: list[str] = Field(
        default_factory=list,
        description=(
            "An explicit list of agent names/patterns to enable. If set, only these"
            " agents will be available. Supports glob patterns (e.g., 'custom-*')"
            " and regex with 're:' prefix."
        ),
    )
    disabled_agents: list[str] = Field(
        default_factory=list,
        description=(
            "A list of agent names/patterns to disable. Ignored if 'enabled_agents'"
            " is set. Supports glob patterns and regex with 're:' prefix."
        ),
    )
    installed_agents: list[str] = Field(
        default_factory=list,
        description=(
            "A list of opt-in builtin agent names that have been explicitly installed."
        ),
    )
    skill_paths: list[Path] = Field(
        default_factory=list,
        description=(
            "Additional directories to search for skills. "
            "Each path may be absolute or relative to the current working directory."
        ),
    )
    enabled_skills: list[str] = Field(
        default_factory=list,
        description=(
            "An explicit list of skill names/patterns to enable. If set, only these"
            " skills will be active. Supports glob patterns (e.g., 'search-*') and"
            " regex with 're:' prefix."
        ),
    )
    disabled_skills: list[str] = Field(
        default_factory=list,
        description=(
            "A list of skill names/patterns to disable. Ignored if 'enabled_skills'"
            " is set. Supports glob patterns and regex with 're:' prefix."
        ),
    )

    model_config = SettingsConfigDict(
        env_prefix="VIBE_", case_sensitive=False, extra="ignore"
    )

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(**kwargs)

    @property
    def nuage_api_key(self) -> str:
        return os.getenv(self.nuage_api_key_env_var, "")

    @property
    def otel_span_exporter_config(self) -> OtelSpanExporterConfig | None:
        # When otel_endpoint is set explicitly, authentication is the user's responsibility
        # (via OTEL_EXPORTER_OTLP_* env vars), so headers are left empty.
        # Otherwise endpoint and API key are derived from the active provider if it's Mistral,
        # or the first Mistral provider.
        traces_export_path = DEFAULT_TRACES_EXPORT_PATH.lstrip("/")
        if self.otel_endpoint:
            return OtelSpanExporterConfig(
                endpoint=urljoin(
                    f"{self.otel_endpoint.rstrip('/')}/", traces_export_path
                )
            )

        provider = self.get_mistral_provider()

        if provider is not None:
            server_url = get_server_url_from_api_base(provider.api_base)
            api_key_env = provider.api_key_env_var or DEFAULT_MISTRAL_API_ENV_KEY
        else:
            server_url = None
            api_key_env = DEFAULT_MISTRAL_API_ENV_KEY

        endpoint = urljoin(
            f"{urljoin(server_url or _DEFAULT_MISTRAL_SERVER_URL, MISTRAL_OTEL_PATH).rstrip('/')}/",
            traces_export_path,
        )

        if not (api_key := os.getenv(api_key_env)):
            logger.warning(
                "OTEL tracing enabled but %s is not set; skipping.", api_key_env
            )
            return None

        return OtelSpanExporterConfig(
            endpoint=endpoint, headers={"Authorization": f"Bearer {api_key}"}
        )

    @property
    def system_prompt(self) -> str:
        try:
            return SystemPrompt[self.system_prompt_id.upper()].read()
        except KeyError:
            pass

        mgr = get_harness_files_manager()
        prompt_dirs = mgr.project_prompts_dirs + mgr.user_prompts_dirs
        for current_prompt_dir in prompt_dirs:
            custom_sp_path = (current_prompt_dir / self.system_prompt_id).with_suffix(
                ".md"
            )
            if custom_sp_path.is_file():
                return read_safe(custom_sp_path).text

        raise MissingPromptFileError(
            self.system_prompt_id, *(str(d) for d in prompt_dirs)
        )

    def get_active_model(self) -> ModelConfig:
        for model in self.models:
            if model.alias == self.active_model:
                return model
        raise ValueError(
            f"Active model '{self.active_model}' not found in configuration."
        )

    def get_compaction_model(self) -> ModelConfig:
        if self.compaction_model is not None:
            return self.compaction_model
        return self.get_active_model()

    def get_mistral_provider(self) -> ProviderConfig | None:
        try:
            active_provider = self.get_provider_for_model(self.get_active_model())
            if active_provider.backend == Backend.MISTRAL:
                return active_provider
        except ValueError:
            pass
        return next((p for p in self.providers if p.backend == Backend.MISTRAL), None)

    def get_provider_for_model(self, model: ModelConfig) -> ProviderConfig:
        for provider in self.providers:
            if provider.name == model.provider:
                return provider
        raise ValueError(
            f"Provider '{model.provider}' for model '{model.name}' not found in configuration."
        )

    def get_active_transcribe_model(self) -> TranscribeModelConfig:
        for model in self.transcribe_models:
            if model.alias == self.active_transcribe_model:
                return model
        raise ValueError(
            f"Active transcribe model '{self.active_transcribe_model}' not found in configuration."
        )

    def get_transcribe_provider_for_model(
        self, model: TranscribeModelConfig
    ) -> TranscribeProviderConfig:
        for provider in self.transcribe_providers:
            if provider.name == model.provider:
                return provider
        raise ValueError(
            f"Transcribe provider '{model.provider}' for transcribe model '{model.name}' not found in configuration."
        )

    def get_active_tts_model(self) -> TTSModelConfig:
        for model in self.tts_models:
            if model.alias == self.active_tts_model:
                return model
        raise ValueError(
            f"Active TTS model '{self.active_tts_model}' not found in configuration."
        )

    def get_tts_provider_for_model(self, model: TTSModelConfig) -> TTSProviderConfig:
        for provider in self.tts_providers:
            if provider.name == model.provider:
                return provider
        raise ValueError(
            f"TTS provider '{model.provider}' for TTS model '{model.name}' not found in configuration."
        )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Define the priority of settings sources.

        Note: dotenv_settings is intentionally excluded. API keys and other
        non-config environment variables are stored in .env but loaded manually
        into os.environ for use by providers. Only VIBE_* prefixed environment
        variables (via env_settings) and TOML config are used for Pydantic settings.
        """
        return (
            init_settings,
            env_settings,
            TomlFileSettingsSource(settings_cls),
            file_secret_settings,
        )

    @model_validator(mode="after")
    def _apply_global_auto_compact_threshold(self) -> VibeConfig:
        self.models = [
            model
            if "auto_compact_threshold" in model.model_fields_set
            else model.model_copy(
                update={"auto_compact_threshold": self.auto_compact_threshold}
            )
            for model in self.models
        ]
        return self

    @model_validator(mode="after")
    def _check_compaction_model_provider(self) -> VibeConfig:
        if self.compaction_model is None:
            return self

        compaction_provider = self.get_provider_for_model(self.compaction_model)
        try:
            active_provider = self.get_provider_for_model(self.get_active_model())
        except ValueError:
            return self
        if active_provider.name != compaction_provider.name:
            raise ValueError(
                f"Compaction model '{self.compaction_model.alias}' uses provider "
                f"'{compaction_provider.name}' but active model uses provider "
                f"'{active_provider.name}'. They must share the same provider."
            )
        return self

    @model_validator(mode="after")
    def _check_api_key(self) -> VibeConfig:
        try:
            active_model = self.get_active_model()
            provider = self.get_provider_for_model(active_model)
            api_key_env = provider.api_key_env_var
            if api_key_env and not os.getenv(api_key_env):
                raise MissingAPIKeyError(api_key_env, provider.name)
        except ValueError:
            pass
        return self

    @field_validator("tool_paths", mode="before")
    @classmethod
    def _expand_tool_paths(cls, v: Any) -> list[Path]:
        if not v:
            return []
        return [Path(p).expanduser().resolve() for p in v]

    @field_validator("skill_paths", mode="before")
    @classmethod
    def _expand_skill_paths(cls, v: Any) -> list[Path]:
        if not v:
            return []
        return [Path(p).expanduser().resolve() for p in v]

    @field_validator("tools", mode="before")
    @classmethod
    def _normalize_tool_configs(cls, v: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(v, dict):
            return {}

        normalized: dict[str, dict[str, Any]] = {}
        for tool_name, tool_config in v.items():
            if isinstance(tool_config, dict):
                normalized[tool_name] = tool_config
            else:
                normalized[tool_name] = {}

        return normalized

    @model_validator(mode="after")
    def _validate_model_uniqueness(self) -> VibeConfig:
        seen_aliases: set[str] = set()
        for model in self.models:
            if model.alias in seen_aliases:
                raise ValueError(
                    f"Duplicate model alias found: '{model.alias}'. Aliases must be unique."
                )
            seen_aliases.add(model.alias)
        return self

    @model_validator(mode="after")
    def _validate_transcribe_model_uniqueness(self) -> VibeConfig:
        seen_aliases: set[str] = set()
        for model in self.transcribe_models:
            if model.alias in seen_aliases:
                raise ValueError(
                    f"Duplicate transcribe model alias found: '{model.alias}'. Aliases must be unique."
                )
            seen_aliases.add(model.alias)
        return self

    @model_validator(mode="after")
    def _validate_tts_model_uniqueness(self) -> VibeConfig:
        seen_aliases: set[str] = set()
        for model in self.tts_models:
            if model.alias in seen_aliases:
                raise ValueError(
                    f"Duplicate TTS model alias found: '{model.alias}'. Aliases must be unique."
                )
            seen_aliases.add(model.alias)
        return self

    @model_validator(mode="after")
    def _check_system_prompt(self) -> VibeConfig:
        _ = self.system_prompt
        return self

    @classmethod
    def save_updates(cls, updates: dict[str, Any]) -> None:
        if not get_harness_files_manager().persist_allowed:
            return
        current_config = TomlFileSettingsSource(cls).toml_data
        merged_config = deep_update(current_config, updates)
        cls.dump_config(merged_config)

    @classmethod
    def dump_config(cls, config: dict[str, Any]) -> None:
        mgr = get_harness_files_manager()
        if not mgr.persist_allowed:
            return
        target = mgr.config_file or mgr.user_config_file
        target.parent.mkdir(parents=True, exist_ok=True)
        toml_document = _to_toml_document(config)
        cls.model_validate(toml_document)
        with target.open("wb") as f:
            tomli_w.dump(toml_document, f)

    @classmethod
    def _migrate(cls) -> None:
        mgr = get_harness_files_manager()
        if not mgr.persist_allowed:
            return
        file = mgr.config_file
        if file is None:
            return
        try:
            with file.open("rb") as f:
                data = tomllib.load(f)
        except (FileNotFoundError, tomllib.TOMLDecodeError, OSError):
            return

        bash_tools = data.get("tools", {}).get("bash", {})
        allowlist = bash_tools.get("allowlist")
        if allowlist is None or "find" not in allowlist:
            return

        allowlist.remove("find")
        cls.dump_config(data)

    @classmethod
    def load(cls, **overrides: Any) -> VibeConfig:
        cls._migrate()
        return cls(**(overrides or {}))

    @classmethod
    def create_default(cls) -> dict[str, Any]:
        config = cls.model_construct()
        config_dict = config.model_dump(mode="json")

        from vibe.core.tools.manager import ToolManager

        tool_defaults = ToolManager.discover_tool_defaults()
        if tool_defaults:
            config_dict["tools"] = tool_defaults

        return config_dict
