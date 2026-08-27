from __future__ import annotations

from collections.abc import Callable, MutableMapping
import json
import os
from pathlib import Path
import tomllib
from typing import Annotated, Any

from dotenv import dotenv_values
from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    PrivateAttr,
    ValidationError,
    ValidationInfo,
    model_validator,
)

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

# DEFAULT_LOG_LEVEL is not imported here to avoid a circular dependency
# (vibe_schema.py imports from vibe.observability.logging). The constant
# lives in vibe.config_values.
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.config.models import (
    ConnectorConfig,
    ExperimentsConfig,
    MCPServer,
    MissingAPIKeyError,
    ModelConfig,
    OtelRedactionMode,
    ProjectContextConfig,
    ProviderConfig,
    SessionLoggingConfig,
    TranscribeModelConfig,
    TranscribeProviderConfig,
    TTSModelConfig,
    TTSProviderConfig,
    normalize_model_configs,
    serialize_model_configs,
)
from vibe.core.config.schema import (
    ConfigSchema,
    WithConcatMerge,
    WithDeepMerge,
    WithReplaceMerge,
    WithUnionMerge,
)
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.core.prompts import (
    SystemPrompt,
    UtilityPrompt,
    load_prompt,
    load_system_prompt,
)
from vibe.core.types import Backend
from vibe.core.utils.matching import name_matches
from vibe.observability.logging import logger
from vibe.utils.api_keys import resolve_api_key


def _strip_bash_pattern_wildcard(pattern: str) -> str:
    if pattern.endswith(" *"):
        return pattern[:-2]
    return pattern


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
    display_name="Mistral Medium 3.5",
    temperature=1.0,
    input_price=1.5,
    output_price=7.5,
    cached_input_price=0.15,
    thinking="high",
    supports_images=True,
)

DEFAULT_MODELS = [
    DEFAULT_ACTIVE_MODEL_CONFIG,
    ModelConfig(
        name="devstral-small-latest",
        provider="mistral",
        alias="devstral-small",
        display_name="Devstral Small",
        input_price=0.1,
        output_price=0.3,
        cached_input_price=0.01,
    ),
    ModelConfig(
        name="devstral",
        provider="llamacpp",
        display_name="Devstral (local)",
        alias="local",
        input_price=0.0,
        output_price=0.0,
    ),
]

# Sentinel ``active_model`` value meaning "not pinned": the config resolves it to
# the default model (see ``get_active_model``). Kept distinct from pinning the
# alias that happens to be the current default, which is a deliberate choice.
UNPINNED_ACTIVE_MODEL = ""

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


def get_persisted_config() -> dict[str, Any]:
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


def _unique_by(key: str) -> Callable[[list[Any]], list[Any]]:
    def check(items: list[Any]) -> list[Any]:
        seen: set[str] = set()
        for item in items:
            value = getattr(item, key)
            if value in seen:
                raise ValueError(f"Duplicate {key} {value!r}; must be unique")
            seen.add(value)
        return items

    return check


def _non_empty(items: list[Any]) -> list[Any]:
    if not items:
        raise ValueError(
            "No models are configured. Define at least one model under [[models]]."
        )
    return items


def _expand_paths(v: Any) -> list[Path]:
    if not v:
        return []
    return [Path(p).expanduser().resolve() for p in v]


def _normalize_tool_configs(v: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(v, dict):
        return {}
    return {name: cfg if isinstance(cfg, dict) else {} for name, cfg in v.items()}


def _coerce_routed_model_config(v: str) -> ModelConfig | None:
    try:
        return ModelConfig.model_validate_json(v)
    except ValidationError:
        return None


def _coerce_routed_extra_models(v: Any) -> list[ModelConfig]:
    """Parse the GrowthBook-supplied extra-models payload, failing open.

    The value arrives as a JSON-array string (like every other GrowthBook-mapped
    config value). Invalid entries are dropped rather than raising so a malformed
    A/B payload can never brick config loading.
    """
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(v, list):
        return []
    coerced: list[ModelConfig] = []
    for item in v:
        try:
            coerced.append(
                item
                if isinstance(item, ModelConfig)
                else ModelConfig.model_validate(item)
            )
        except ValidationError:
            continue
    return coerced


_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def _normalize_log_level(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip().upper()
    if normalized not in _LOG_LEVELS:
        raise ValueError(
            f"Invalid log level {value!r}; expected one of {sorted(_LOG_LEVELS)}"
        )
    return normalized


class VibeConfigSchema(ConfigSchema):
    _validation_warnings: list[str] = PrivateAttr(default_factory=list)

    @property
    def validation_warnings(self) -> tuple[str, ...]:
        return tuple(self._validation_warnings)

    # Models
    active_model: Annotated[str, WithReplaceMerge()] = UNPINNED_ACTIVE_MODEL
    # Experiment-routed default model alias, populated at runtime by the
    # GrowthBook layer. It only takes effect when ``active_model`` is unpinned.
    routed_default_model: Annotated[str, WithReplaceMerge()] = ""
    # Full definition of ``routed_default_model``, also supplied at runtime by the GrowthBook layer.
    routed_model_config: Annotated[
        ModelConfig | None,
        WithReplaceMerge(),
        BeforeValidator(_coerce_routed_model_config),
    ] = None
    # Extra models injected into the dropdown at runtime by the GrowthBook layer,
    # for exposure/rollout without changing the default. Unlike
    # ``routed_model_config``, these never influence ``resolve_default_model_alias``.
    routed_extra_models: Annotated[
        list[ModelConfig],
        WithReplaceMerge(),
        BeforeValidator(_coerce_routed_extra_models),
    ] = Field(default_factory=list)
    providers: Annotated[list[ProviderConfig], WithUnionMerge(merge_key="name")] = (
        Field(default_factory=lambda: list(DEFAULT_PROVIDERS))
    )
    models: Annotated[
        dict[str, ModelConfig],
        # Keyed by alias internally so per-model patches can deep-merge.
        # Sparse default-model overrides are completed by DefaultConfigLayer at
        # merge time; here we only normalize the list / alias map into the map shape.
        WithDeepMerge(),
        BeforeValidator(normalize_model_configs),
        AfterValidator(_non_empty),
    ] = Field(default_factory=lambda: normalize_model_configs(DEFAULT_MODELS))
    allowed_models: Annotated[list[str], WithReplaceMerge()] = Field(
        default_factory=list,
        description=(
            "An explicit list of model aliases/patterns to allow. If set, only these"
            " models are selectable. An empty list allows all configured models."
            " Supports glob patterns (e.g., 'mistral-*') and regex with 're:' prefix."
        ),
    )
    compaction_model: Annotated[ModelConfig | None, WithReplaceMerge()] = None
    auto_compact_threshold: Annotated[int, WithReplaceMerge()] = (
        DEFAULT_AUTO_COMPACT_THRESHOLD
    )
    active_transcribe_model: Annotated[str, WithReplaceMerge()] = (
        DEFAULT_ACTIVE_TRANSCRIBE_MODEL_CONFIG.alias
    )
    transcribe_providers: Annotated[
        list[TranscribeProviderConfig], WithUnionMerge(merge_key="name")
    ] = Field(default_factory=lambda: list(DEFAULT_TRANSCRIBE_PROVIDERS))
    transcribe_models: Annotated[
        list[TranscribeModelConfig],
        WithUnionMerge(merge_key="alias"),
        AfterValidator(_unique_by("alias")),
    ] = Field(default_factory=lambda: list(DEFAULT_TRANSCRIBE_MODELS))
    active_tts_model: Annotated[str, WithReplaceMerge()] = (
        DEFAULT_ACTIVE_TTS_MODEL_CONFIG.alias
    )
    tts_providers: Annotated[
        list[TTSProviderConfig], WithUnionMerge(merge_key="name")
    ] = Field(default_factory=lambda: list(DEFAULT_TTS_PROVIDERS))
    tts_models: Annotated[
        list[TTSModelConfig],
        WithUnionMerge(merge_key="alias"),
        AfterValidator(_unique_by("alias")),
    ] = Field(default_factory=lambda: list(DEFAULT_TTS_MODELS))

    # Tools
    tools: Annotated[
        dict[str, dict[str, Any]],
        WithDeepMerge(),
        BeforeValidator(_normalize_tool_configs),
    ] = Field(default_factory=dict)
    tool_paths: Annotated[
        list[Path], WithConcatMerge(), BeforeValidator(_expand_paths)
    ] = Field(
        default_factory=list,
        description=(
            "Additional directories or files to explore for custom tools. "
            "Paths may be absolute or relative to the current working directory. "
            "Directories are shallow-searched for tool definition files, "
            "while files are loaded directly if valid."
        ),
    )
    enabled_tools: Annotated[list[str], WithReplaceMerge()] = Field(
        default_factory=list,
        description=(
            "An explicit list of tool names/patterns to enable. If set, only these"
            " tools will be active. Supports glob patterns (e.g., 'serena_*') and"
            " regex with 're:' prefix (e.g., 're:^serena_.*')."
        ),
    )
    disabled_tools: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list,
        description=(
            "A list of tool names/patterns to disable after 'enabled_tools' filtering. "
            "Supports glob patterns and regex with 're:' prefix."
        ),
    )
    mcp_servers: Annotated[
        list[MCPServer],
        WithUnionMerge(merge_key="name"),
        AfterValidator(_unique_by("name")),
    ] = Field(
        default_factory=list, description="Preferred MCP server configuration entries."
    )
    enable_connectors: Annotated[bool, WithReplaceMerge()] = True
    connectors: Annotated[list[ConnectorConfig], WithUnionMerge(merge_key="name")] = (
        Field(
            default_factory=list,
            description="Per-connector settings (disable, disabled_tools).",
        )
    )

    # Agents
    agent_paths: Annotated[list[Path], WithConcatMerge()] = Field(
        default_factory=list,
        description=(
            "Additional directories to search for custom agent profiles. "
            "Each path may be absolute or relative to the current working directory."
        ),
    )
    enabled_agents: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list,
        description=(
            "An explicit list of agent names/patterns to enable. If set, only these"
            " agents will be available. Supports glob patterns (e.g., 'custom-*')"
            " and regex with 're:' prefix."
        ),
    )
    disabled_agents: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list,
        description=(
            "A list of agent names/patterns to disable. Ignored if 'enabled_agents'"
            " is set. Supports glob patterns and regex with 're:' prefix."
        ),
    )
    installed_agents: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list,
        description=(
            "A list of opt-in builtin agent names that have been explicitly installed."
        ),
    )
    default_agent: Annotated[str, WithReplaceMerge()] = Field(
        default=BuiltinAgentName.ACCEPT_EDITS,
        description=(
            "Agent profile to use when no --agent flag is passed. "
            "Builtin: ask, plan, accept-edits, auto-approve. "
            "Applies in both interactive and programmatic (-p/--prompt) mode."
        ),
    )

    # Skills
    skill_paths: Annotated[
        list[Path], WithConcatMerge(), BeforeValidator(_expand_paths)
    ] = Field(
        default_factory=list,
        description=(
            "Additional directories to search for skills. "
            "Each path may be absolute or relative to the current working directory."
        ),
    )
    enabled_skills: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list,
        description=(
            "An explicit list of skill names/patterns to enable. If set, only these"
            " skills will be active. Supports glob patterns (e.g., 'search-*') and"
            " regex with 're:' prefix."
        ),
    )
    disabled_skills: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list,
        description=(
            "A list of skill names/patterns to disable. Ignored if 'enabled_skills'"
            " is set. Supports glob patterns and regex with 're:' prefix."
        ),
    )
    experimental_enable_registry_skills: Annotated[bool, WithReplaceMerge()] = Field(
        default=False,
        description=(
            "Experimental: pull workspace skills from the Mistral AI Registry"
            " (api.mistral.ai) and make them available alongside local skills."
            " Requires a Mistral provider and API key. Local and builtin skills take"
            " precedence on name collision."
        ),
    )

    # Internal
    vibe_code_enabled: Annotated[bool, WithReplaceMerge()] = True
    vibe_code_api_key_env_var: Annotated[str, WithReplaceMerge()] = (
        DEFAULT_MISTRAL_API_ENV_KEY
    )

    # Tracing
    enable_otel: Annotated[bool, WithReplaceMerge()] = Field(
        default=False,
        description=(
            "Export OpenTelemetry traces for agent, model, and tool operations. "
            "Requires enable_telemetry to be enabled."
        ),
    )
    otel_endpoint: Annotated[str, WithReplaceMerge()] = Field(
        default="",
        description=(
            "Base URL of a custom OTLP/HTTP collector. Vibe appends /v1/traces. "
            "When empty, Vibe uses the configured Mistral telemetry endpoint."
        ),
    )
    otel_redaction: Annotated[OtelRedactionMode, WithReplaceMerge()] = Field(
        default=OtelRedactionMode.DEFAULT,
        description=(
            "Client-side span attribute redaction: default redacts sensitive "
            "values, strict redacts sensitive attributes, and none disables "
            "redaction."
        ),
    )
    console_base_url: Annotated[str, WithReplaceMerge()] = DEFAULT_CONSOLE_BASE_URL

    # Top-level scalars
    theme: Annotated[str, WithReplaceMerge()] = DEFAULT_THEME
    applied_migrations: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list
    )
    disable_welcome_banner_animation: Annotated[bool, WithReplaceMerge()] = False
    show_greeting: Annotated[bool, WithReplaceMerge()] = Field(
        default=True,
        description="Show greeting at startup (Mistral providers only, once per 24h).",
    )
    autocopy_to_clipboard: Annotated[bool, WithReplaceMerge()] = True
    file_watcher_for_autocomplete: Annotated[bool, WithReplaceMerge()] = False
    ask_confirmation_on_exit: Annotated[bool, WithReplaceMerge()] = True
    displayed_workdir: Annotated[str, WithReplaceMerge()] = ""
    context_warnings: Annotated[bool, WithReplaceMerge()] = False
    voice_mode_enabled: Annotated[bool, WithReplaceMerge()] = False
    narrator_enabled: Annotated[bool, WithReplaceMerge()] = False
    show_thinking_nodes: Annotated[bool, WithReplaceMerge()] = False
    bypass_tool_permissions: Annotated[bool, WithReplaceMerge()] = False
    raise_on_compaction_failure: Annotated[bool, WithReplaceMerge()] = False
    enable_telemetry: Annotated[bool, WithReplaceMerge()] = True
    system_prompt_id: Annotated[str, WithReplaceMerge()] = SystemPrompt.CLI
    managed_shell_tools_enabled: Annotated[bool, WithReplaceMerge()] = False
    compaction_prompt_id: Annotated[str, WithReplaceMerge()] = UtilityPrompt.COMPACT
    include_commit_signature: Annotated[bool, WithReplaceMerge()] = True
    include_model_info: Annotated[bool, WithReplaceMerge()] = True
    include_project_context: Annotated[bool, WithReplaceMerge()] = True
    include_prompt_detail: Annotated[bool, WithReplaceMerge()] = True
    enable_update_checks: Annotated[bool, WithReplaceMerge()] = True
    enable_auto_update: Annotated[bool, WithReplaceMerge()] = True
    enable_notifications: Annotated[bool, WithReplaceMerge()] = True
    enable_system_trust_store: Annotated[bool, WithReplaceMerge()] = False
    api_timeout: Annotated[float, WithReplaceMerge()] = DEFAULT_API_TIMEOUT
    api_retry_max_elapsed_time: Annotated[float, WithReplaceMerge()] = (
        DEFAULT_API_RETRY_MAX_ELAPSED_TIME
    )
    vibe_base_url: Annotated[str, WithReplaceMerge()] = DEFAULT_VIBE_BASE_URL
    vibe_code_sessions_base_url: Annotated[str, WithReplaceMerge()] = (
        "https://chat.mistral.ai"
    )
    log_level: Annotated[
        str | None, WithReplaceMerge(), BeforeValidator(_normalize_log_level)
    ] = None

    # Nested configs (REPLACE — simple nested models, no merge semantics)
    project_context: Annotated[ProjectContextConfig, WithReplaceMerge()] = Field(
        default_factory=ProjectContextConfig
    )
    session_logging: Annotated[SessionLoggingConfig, WithReplaceMerge()] = Field(
        default_factory=SessionLoggingConfig
    )
    experiments: Annotated[ExperimentsConfig, WithReplaceMerge()] = Field(
        default_factory=ExperimentsConfig
    )

    def resolve_default_model_alias(self) -> str:
        available = self.available_models()
        if self.routed_default_model and self.routed_default_model in available:
            return self.routed_default_model
        if DEFAULT_ACTIVE_MODEL_CONFIG.alias in available or not available:
            # If there are no available models, fallback to default
            return DEFAULT_ACTIVE_MODEL_CONFIG.alias
        return next(iter(available))

    def available_models(self) -> dict[str, ModelConfig]:
        if not self.allowed_models:
            return self.models
        allowed = {
            alias: model
            for alias, model in self.models.items()
            if name_matches(alias, self.allowed_models)
        }
        # A filter that matches nothing degrades to "allow all" rather than
        # bricking model selection; the mismatch already surfaces as a warning.
        return allowed or self.models

    def get_active_model(self) -> ModelConfig:
        if self.active_model and self.active_model not in self.models:
            raise ValueError(
                f"Active model '{self.active_model}' not found in configuration."
            )
        # An allowlist-excluded pin never runs: fall back to the default allowed model.
        available = self.available_models()
        alias = (
            self.active_model
            if self.active_model in available
            else self.resolve_default_model_alias()
        )
        if model := available.get(alias):
            return model
        raise ValueError(
            f"Active model '{self.active_model}' not found in configuration."
        )

    def get_provider_for_model(self, model: ModelConfig) -> ProviderConfig:
        if provider := next(
            (p for p in self.providers if p.name == model.provider), None
        ):
            return provider
        raise ValueError(
            f"Provider '{model.provider}' for model '{model.name}' not found in configuration."
        )

    @property
    def vibe_code_api_key(self) -> str:
        return resolve_api_key(self.vibe_code_api_key_env_var) or ""

    def get_compaction_model(self) -> ModelConfig:
        if self.compaction_model is not None:
            return self.compaction_model
        return self.get_active_model()

    def connectors_by_name(self) -> dict[str, ConnectorConfig]:
        return {c.name: c for c in self.connectors}

    def get_active_provider(self) -> ProviderConfig:
        return self.get_provider_for_model(self.get_active_model())

    def get_mistral_provider(self) -> ProviderConfig | None:
        try:
            active_provider = self.get_active_provider()
            if active_provider.backend == Backend.MISTRAL:
                return active_provider
        except ValueError:
            pass
        return next((p for p in self.providers if p.backend == Backend.MISTRAL), None)

    def is_active_model_mistral(self) -> bool:
        try:
            return self.get_active_provider().backend == Backend.MISTRAL
        except ValueError:
            return False

    def get_active_transcribe_model(self) -> TranscribeModelConfig:
        if model := next(
            (
                m
                for m in self.transcribe_models
                if m.alias == self.active_transcribe_model
            ),
            None,
        ):
            return model
        raise ValueError(
            f"Active transcribe model '{self.active_transcribe_model}' not found in configuration."
        )

    def get_transcribe_provider_for_model(
        self, model: TranscribeModelConfig
    ) -> TranscribeProviderConfig:
        if provider := next(
            (p for p in self.transcribe_providers if p.name == model.provider), None
        ):
            return provider
        raise ValueError(
            f"Transcribe provider '{model.provider}' for transcribe model '{model.name}' not found in configuration."
        )

    def get_active_tts_model(self) -> TTSModelConfig:
        if model := next(
            (m for m in self.tts_models if m.alias == self.active_tts_model), None
        ):
            return model
        raise ValueError(
            f"Active TTS model '{self.active_tts_model}' not found in configuration."
        )

    def get_tts_provider_for_model(self, model: TTSModelConfig) -> TTSProviderConfig:
        if provider := next(
            (p for p in self.tts_providers if p.name == model.provider), None
        ):
            return provider
        raise ValueError(
            f"TTS provider '{model.provider}' for TTS model '{model.name}' not found in configuration."
        )

    def build_tool_allowlist_update(
        self,
        tool_name: str,
        patterns: list[str],
        *,
        current_allowlist: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Extend a tool's allowlist in memory and return the persist payload.

        Returns ``None`` when every pattern is already allowlisted. Callers
        persist the returned payload; the in-memory config is kept current so
        repeated calls merge from fresh state.
        """
        if tool_name == "bash":
            patterns = [_strip_bash_pattern_wildcard(p) for p in patterns]
        allowlist: list[str] = list(
            current_allowlist
            if current_allowlist is not None
            else self.tools.get(tool_name, {}).get("allowlist", [])
        )
        new_patterns = [p for p in patterns if p not in allowlist]
        if not new_patterns:
            return None
        merged = sorted(allowlist + new_patterns)
        self.tools.setdefault(tool_name, {})["allowlist"] = merged
        return {"tools": {tool_name: {"allowlist": merged}}}

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

    @model_validator(mode="after")
    def _inject_routed_model(self) -> VibeConfigSchema:
        alias = self.routed_default_model
        model = self.routed_model_config
        if not (alias and model is not None and model.alias == alias):
            return self

        existing = self.models.get(alias)
        if existing is None:
            object.__setattr__(self, "models", {**self.models, alias: model})
        else:
            user_overrides = {
                field: getattr(existing, field) for field in existing.model_fields_set
            }
            object.__setattr__(
                self,
                "models",
                {**self.models, alias: model.model_copy(update=user_overrides)},
            )
        return self

    @model_validator(mode="after")
    def _inject_routed_extra_models(self) -> VibeConfigSchema:
        # Adds models to the dropdown without touching ``routed_default_model``,
        # so exposure/rollout never changes the resolved default. Composes with
        # ``_inject_routed_model``: if ``routed_default_model`` names an alias
        # added here, ``resolve_default_model_alias`` still promotes it to default.
        if not self.routed_extra_models:
            return self
        models = dict(self.models)
        for model in self.routed_extra_models:
            # An empty alias collides with the unpinned ``active_model`` sentinel
            # (""), so a malformed payload could hijack the resolved default. Skip
            # it: injection must never touch the default.
            if not model.alias:
                continue
            existing = models.get(model.alias)
            if existing is None:
                models[model.alias] = model
                continue
            # A user-defined entry for the same alias always wins over injection.
            user_overrides = {
                field: getattr(existing, field) for field in existing.model_fields_set
            }
            models[model.alias] = model.model_copy(update=user_overrides)
        object.__setattr__(self, "models", models)
        return self

    @model_validator(mode="after")
    def _apply_global_auto_compact_threshold(self) -> VibeConfigSchema:
        models = {
            alias: (
                model
                if "auto_compact_threshold" in model.model_fields_set
                else model.model_copy(
                    update={"auto_compact_threshold": self.auto_compact_threshold}
                )
            )
            for alias, model in self.models.items()
        }
        object.__setattr__(self, "models", models)
        return self

    @model_validator(mode="after")
    def _apply_active_model_fallback(self) -> VibeConfigSchema:
        # The empty string is the "unpinned/default" sentinel: it is always valid
        # and resolves to a configured model at read time (get_active_model).
        if self.active_model and self.active_model not in self.models:
            unknown = self.active_model
            fallback = next(iter(self.models))
            logger.warning(
                "Active model '%s' is not in your configured models; defaulting to '%s'.",
                unknown,
                fallback,
            )
            self._validation_warnings.append(
                f"Active model '{unknown}' is not in your configured models "
                f"— defaulting to '{fallback}'."
            )
            object.__setattr__(self, "active_model", fallback)
        return self

    @model_validator(mode="after")
    def _warn_unmatched_allowed_models(self) -> VibeConfigSchema:
        for pattern in self.allowed_models:
            if not (pattern or "").strip():
                continue
            if any(name_matches(alias, [pattern]) for alias in self.models):
                continue
            logger.warning(
                "Allowed model '%s' matches none of your configured models.", pattern
            )
            self._validation_warnings.append(
                f"Allowed model '{pattern}' matches none of your configured models."
            )
        return self

    @model_validator(mode="after")
    def _check_compaction_model_provider(self) -> VibeConfigSchema:
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
    def _check_api_key(self, info: ValidationInfo) -> VibeConfigSchema:
        if info.context is not None and not info.context.get("require_api_key", True):
            return self
        try:
            provider = self.get_provider_for_model(self.get_active_model())
            api_key_env = provider.api_key_env_var
            if api_key_env and not resolve_api_key(api_key_env):
                raise MissingAPIKeyError(api_key_env, provider.name)
        except ValueError:
            pass
        return self

    @model_validator(mode="after")
    def _check_system_prompt(self) -> VibeConfigSchema:
        _ = self.system_prompt
        return self

    @model_validator(mode="after")
    def _check_compaction_prompt(self) -> VibeConfigSchema:
        _ = self.compaction_prompt
        return self


def create_default_config() -> dict[str, Any]:
    from vibe.core.tools.manager import ToolManager

    config_dict = VibeConfigSchema.model_construct().model_dump(
        mode="json", exclude_none=True
    )
    if isinstance(config_dict.get("models"), dict):
        config_dict["models"] = serialize_model_configs(config_dict["models"])
    if tool_defaults := ToolManager.discover_tool_defaults():
        config_dict["tools"] = tool_defaults
    return config_dict
