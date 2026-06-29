from __future__ import annotations

from collections.abc import MutableMapping
import os
from pathlib import Path
import tomllib
from typing import Any, ClassVar

from dotenv import dotenv_values
from pydantic import Field, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_core import to_jsonable_python
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from textual.theme import BUILTIN_THEMES
import tomli_w

from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config._defaults import (
    DEFAULT_API_RETRY_MAX_ELAPSED_TIME,
    DEFAULT_API_TIMEOUT,
    DEFAULT_AUTO_COMPACT_THRESHOLD,
    DEFAULT_CONSOLE_BASE_URL,
    DEFAULT_MISTRAL_API_ENV_KEY,
    DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
    DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
    DEFAULT_MISTRAL_SERVER_URL,
    DEFAULT_THEME,
    DEFAULT_VIBE_BASE_URL,
)
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.config.models import (
    THINKING_LEVELS as THINKING_LEVELS,
    ConnectorConfig,
    ExperimentsConfig,
    MCPServer,
    MissingAPIKeyError,
    ModelConfig,
    ProjectContextConfig,
    ProviderConfig,
    SessionLoggingConfig,
    ThinkingLevel,
    TranscribeModelConfig,
    TranscribeProviderConfig,
    TTSModelConfig,
    TTSProviderConfig,
)
from vibe.core.logger import logger
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.core.prompts import (
    SystemPrompt,
    UtilityPrompt,
    load_prompt,
    load_system_prompt,
)
from vibe.core.types import Backend
from vibe.core.utils import configure_ssl_context
from vibe.core.utils.keyring import get_api_key_from_keyring


def _strip_bash_pattern_wildcard(pattern: str) -> str:
    if pattern.endswith(" *"):
        return pattern[:-2]
    return pattern


def deep_update(
    mapping: dict[str, Any], updating_mapping: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(mapping)
    for key, value in updating_mapping.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_update(merged[key], value)
        else:
            merged[key] = value
    return merged


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
        if environ.get(key):
            # An explicit non-empty process/shell value wins over the .env file.
            continue
        environ[key] = value


def resolve_api_key(env_key: str) -> str | None:
    """Resolve an API key value: process/.env environment first, then OS keyring."""
    if not env_key:
        return None
    value = os.environ.get(env_key)
    if value:
        return value
    return get_api_key_from_keyring(env_key)


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


DEFAULT_PROVIDERS = [
    ProviderConfig(
        name="mistral",
        api_base=f"{DEFAULT_MISTRAL_SERVER_URL}/v1",
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

DEFAULT_ACTIVE_MODEL_CONFIG = ModelConfig(
    name="mistral-vibe-cli-latest",
    provider="mistral",
    alias="mistral-medium-3.5",
    temperature=1.0,
    input_price=1.5,
    output_price=7.5,
    thinking="high",
    supports_images=True,
)

DEFAULT_MODELS = [
    DEFAULT_ACTIVE_MODEL_CONFIG,
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

DEFAULT_TRANSCRIBE_PROVIDERS = [
    TranscribeProviderConfig(
        name="mistral",
        api_base="wss://api.mistral.ai",
        api_key_env_var=DEFAULT_MISTRAL_API_ENV_KEY,
    )
]

DEFAULT_ACTIVE_TRANSCRIBE_MODEL_CONFIG = TranscribeModelConfig(
    name="voxtral-mini-transcribe-realtime-2602",
    provider="mistral",
    alias="voxtral-realtime",
)

DEFAULT_TRANSCRIBE_MODELS = [DEFAULT_ACTIVE_TRANSCRIBE_MODEL_CONFIG]

DEFAULT_TTS_PROVIDERS = [
    TTSProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai",
        api_key_env_var=DEFAULT_MISTRAL_API_ENV_KEY,
    )
]

DEFAULT_ACTIVE_TTS_MODEL_CONFIG = TTSModelConfig(
    name="voxtral-mini-tts-latest", provider="mistral", alias="voxtral-tts"
)

DEFAULT_TTS_MODELS = [DEFAULT_ACTIVE_TTS_MODEL_CONFIG]


def resolve_theme_name(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return DEFAULT_THEME
    if value not in BUILTIN_THEMES:
        logger.warning("Unknown theme=%s; falling back to %s", value, DEFAULT_THEME)
        return DEFAULT_THEME
    return value


class VibeConfig(BaseSettings):
    active_model: str = DEFAULT_ACTIVE_MODEL_CONFIG.alias
    theme: str = DEFAULT_THEME
    disable_welcome_banner_animation: bool = False
    autocopy_to_clipboard: bool = True
    file_watcher_for_autocomplete: bool = False
    displayed_workdir: str = ""
    context_warnings: bool = False
    voice_mode_enabled: bool = False
    narrator_enabled: bool = False
    active_transcribe_model: str = DEFAULT_ACTIVE_TRANSCRIBE_MODEL_CONFIG.alias
    active_tts_model: str = DEFAULT_ACTIVE_TTS_MODEL_CONFIG.alias
    bypass_tool_permissions: bool = False
    raise_on_compaction_failure: bool = False
    enable_telemetry: bool = True
    experiment_overrides: dict[str, str] = Field(default_factory=dict)
    applied_migrations: list[str] = Field(default_factory=list, exclude=True)
    system_prompt_id: str = SystemPrompt.CLI
    compaction_prompt_id: str = UtilityPrompt.COMPACT
    include_commit_signature: bool = True
    include_model_info: bool = True
    include_project_context: bool = True
    include_prompt_detail: bool = True
    enable_update_checks: bool = True
    enable_notifications: bool = True
    enable_system_trust_store: bool = False
    api_timeout: float = DEFAULT_API_TIMEOUT
    api_retry_max_elapsed_time: float = DEFAULT_API_RETRY_MAX_ELAPSED_TIME
    auto_compact_threshold: int = DEFAULT_AUTO_COMPACT_THRESHOLD

    vibe_code_enabled: bool = Field(default=True, exclude=True)
    vibe_code_sessions_base_url: str = Field(
        default="https://chat.mistral.ai", exclude=True
    )
    vibe_code_api_key_env_var: str = Field(
        default=DEFAULT_MISTRAL_API_ENV_KEY, exclude=True
    )
    vibe_code_project_name: str | None = Field(default=None, exclude=True)

    # TODO(otel): remove exclude=True once the feature is publicly available
    enable_otel: bool = Field(default=False, exclude=True)
    otel_endpoint: str = Field(default="", exclude=True)

    console_base_url: str = Field(default=DEFAULT_CONSOLE_BASE_URL, exclude=True)
    vibe_base_url: str = Field(default=DEFAULT_VIBE_BASE_URL, exclude=True)

    enable_experimental_hooks: bool = Field(default=False, exclude=True)

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
    experiments: ExperimentsConfig = Field(default_factory=ExperimentsConfig)
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
    enable_connectors: bool = Field(
        default=True,
        description=(
            "Master switch for Mistral connectors. When False, no connector "
            "tools are discovered or registered, regardless of provider/API key."
        ),
    )
    connectors: list[ConnectorConfig] = Field(
        default_factory=list,
        description="Per-connector settings (disable, disabled_tools).",
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
    default_agent: str = Field(
        default=BuiltinAgentName.DEFAULT,
        description=(
            "Agent profile to use when no --agent flag is passed. "
            "Builtin: default, plan, accept-edits, auto-approve. "
            "Applies in both interactive and programmatic (-p/--prompt) mode."
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
    experimental_enable_registry_skills: bool = Field(
        default=False,
        description=(
            "Experimental: pull workspace skills from the Mistral AI Registry"
            " (api.mistral.ai) and make them available alongside local skills."
            " Requires a Mistral provider and API key. Local and builtin skills take"
            " precedence on name collision."
        ),
    )

    model_config = SettingsConfigDict(
        env_prefix="VIBE_", case_sensitive=False, extra="ignore"
    )

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(**kwargs)

    @property
    def vibe_code_api_key(self) -> str:
        return resolve_api_key(self.vibe_code_api_key_env_var) or ""

    @property
    def system_prompt(self) -> str:
        return load_system_prompt(self.system_prompt_id)

    @property
    def compaction_prompt(self) -> str:
        return load_prompt(
            self.compaction_prompt_id,
            setting_name="compaction_prompt_id",
            builtins={"compact": UtilityPrompt.COMPACT.path},
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

    def connectors_by_name(self) -> dict[str, ConnectorConfig]:
        return {c.name: c for c in self.connectors}

    def get_mistral_provider(self) -> ProviderConfig | None:
        try:
            active_provider = self.get_active_provider()
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

    def get_active_provider(self) -> ProviderConfig:
        return self.get_provider_for_model(self.get_active_model())

    def is_active_model_mistral(self) -> bool:
        try:
            return self.get_active_provider().backend == Backend.MISTRAL
        except ValueError:
            return False

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
            (
                model
                if "auto_compact_threshold" in model.model_fields_set
                else model.model_copy(
                    update={"auto_compact_threshold": self.auto_compact_threshold}
                )
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
            active_provider = self.get_active_provider()
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
            provider = self.get_active_provider()
            api_key_env = provider.api_key_env_var
            if api_key_env and not resolve_api_key(api_key_env):
                raise MissingAPIKeyError(api_key_env, provider.name)
        except ValueError:
            pass
        return self

    @field_validator("theme", mode="before")
    @classmethod
    def _validate_theme(cls, v: Any) -> str:
        return resolve_theme_name(v)

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

    @model_validator(mode="after")
    def _check_compaction_prompt(self) -> VibeConfig:
        _ = self.compaction_prompt
        return self

    def set_thinking(self, level: ThinkingLevel) -> None:
        model = self.get_active_model()

        for i, m in enumerate(self.models):
            if m.alias == model.alias:
                self.models[i] = m.model_copy(update={"thinking": level})
                break

        current_config = TomlFileSettingsSource(type(self)).toml_data
        models = current_config.get("models", [])
        for entry in models:
            if entry.get("alias", entry.get("name")) == model.alias:
                entry["thinking"] = level
                break
        else:
            # Model comes from defaults; materialize the identities so we
            # don't lose the other models.
            models = [
                {
                    "name": m.name,
                    "provider": m.provider,
                    "alias": m.alias,
                    "thinking": level if m.alias == model.alias else m.thinking,
                    **({"supports_images": True} if m.supports_images else {}),
                }
                for m in self.models
            ]
        type(self).save_updates({"models": models})

    def add_tool_allowlist_patterns(self, tool_name: str, patterns: list[str]) -> None:
        if tool_name == "bash":
            patterns = [_strip_bash_pattern_wildcard(p) for p in patterns]
        current_allowlist: list[str] = list(
            self.tools.get(tool_name, {}).get("allowlist", [])
        )
        new_patterns = [p for p in patterns if p not in current_allowlist]
        if not new_patterns:
            return
        merged = sorted(current_allowlist + new_patterns)
        self.save_updates({"tools": {tool_name: {"allowlist": merged}}})
        if tool_name not in self.tools:
            self.tools[tool_name] = {}
        self.tools[tool_name]["allowlist"] = merged

    @classmethod
    def get_persisted_config(cls) -> dict[str, Any]:
        return TomlFileSettingsSource(cls).toml_data

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
        jsonable = to_jsonable_python(config, fallback=str)
        if not isinstance(jsonable, dict):
            toml_document = {}
        else:
            toml_document = _remove_none_values(jsonable)
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

        changed = False

        bash_tools = data.get("tools", {}).get("bash", {})
        allowlist = bash_tools.get("allowlist")
        if allowlist is not None and "find" not in allowlist:
            allowlist.append("find")
            allowlist.sort()
            changed = True

        if allowlist is not None and any(p.endswith(" *") for p in allowlist):
            stripped = [_strip_bash_pattern_wildcard(p) for p in allowlist]
            deduped = sorted(set(stripped))
            bash_tools["allowlist"] = deduped
            allowlist = deduped
            changed = True

        applied: list[str] = data.get("applied_migrations", [])
        if allowlist is not None and cls._BASH_READ_ONLY_MIGRATION not in applied:
            from vibe.core.tools.builtins.bash import default_read_only_commands

            bash_tools["allowlist"] = sorted(
                set(allowlist) | set(default_read_only_commands())
            )
            data["applied_migrations"] = [*applied, cls._BASH_READ_ONLY_MIGRATION]
            changed = True

        for model in data.get("models", []):
            if (
                model.get("name") == "mistral-vibe-cli-latest"
                and model.get("alias") == "devstral-2"
            ):
                model["alias"] = "mistral-medium-3.5"
                model["temperature"] = 1.0
                model["input_price"] = 1.5
                model["output_price"] = 7.5
                model["thinking"] = "high"
                changed = True

            if (
                model.get("name") == "mistral-vibe-cli-latest"
                and model.get("alias") == "mistral-medium-3.5"
                and "supports_images" not in model
            ):
                model["supports_images"] = True
                changed = True

        if data.get("active_model") == "devstral-2":
            data["active_model"] = "mistral-medium-3.5"
            changed = True

        if cls._migrate_renamed_tools(data):
            changed = True

        if changed:
            cls.dump_config(data)

    # One-shot id: syncs an existing bash allowlist up to the current default
    # read-only commands once, so users keep the ability to remove any of them.
    _BASH_READ_ONLY_MIGRATION: ClassVar[str] = "bash_read_only_defaults_v1"

    # Old tool name -> new tool name. The new tools replaced these in-place, so
    # existing user configs keyed by the old names need their settings moved over.
    _RENAMED_TOOLS: ClassVar[dict[str, str]] = {
        "read_file": "read",
        "search_replace": "edit",
    }
    # Options on the old tool that have no equivalent on the new one; dropped on migrate.
    _DROPPED_TOOL_OPTIONS: ClassVar[dict[str, tuple[str, ...]]] = {
        "edit": ("max_content_size", "create_backup")
    }

    @classmethod
    def _migrate_renamed_tools(cls, data: dict[str, Any]) -> bool:
        changed = False

        tools = data.get("tools")
        if isinstance(tools, dict):
            for old, new in cls._RENAMED_TOOLS.items():
                if old not in tools:
                    continue
                old_config = tools.pop(old)
                changed = True
                # Prefer an already-present new key; don't clobber it.
                if new not in tools:
                    if isinstance(old_config, dict):
                        for dropped in cls._DROPPED_TOOL_OPTIONS.get(new, ()):
                            old_config.pop(dropped, None)
                    tools[new] = old_config

        for list_key in ("enabled_tools", "disabled_tools"):
            names = data.get(list_key)
            if not isinstance(names, list):
                continue
            renamed = [cls._RENAMED_TOOLS.get(name, name) for name in names]
            if renamed != names:
                data[list_key] = renamed
                changed = True

        return changed

    @classmethod
    def load(cls, **overrides: Any) -> VibeConfig:
        cls._migrate()
        config = cls(**(overrides or {}))
        configure_ssl_context(
            enable_system_trust_store=config.enable_system_trust_store
        )
        return config

    @classmethod
    def create_default(cls) -> dict[str, Any]:
        config = cls.model_construct()
        config_dict = config.model_dump(mode="json")

        from vibe.core.tools.manager import ToolManager

        tool_defaults = ToolManager.discover_tool_defaults()
        if tool_defaults:
            config_dict["tools"] = tool_defaults

        return config_dict
