from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from pydantic import AfterValidator, BeforeValidator, Field, model_validator

from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config._defaults import (
    DEFAULT_API_RETRY_MAX_ELAPSED_TIME,
    DEFAULT_API_TIMEOUT,
    DEFAULT_AUTO_COMPACT_THRESHOLD,
    DEFAULT_CONSOLE_BASE_URL,
    DEFAULT_MISTRAL_API_ENV_KEY,
    DEFAULT_THEME,
    DEFAULT_VIBE_BASE_URL,
)
from vibe.core.config._settings import (
    DEFAULT_ACTIVE_MODEL_CONFIG,
    DEFAULT_ACTIVE_TRANSCRIBE_MODEL_CONFIG,
    DEFAULT_ACTIVE_TTS_MODEL_CONFIG,
    DEFAULT_MODELS,
    DEFAULT_PROVIDERS,
    DEFAULT_TRANSCRIBE_MODELS,
    DEFAULT_TRANSCRIBE_PROVIDERS,
    DEFAULT_TTS_MODELS,
    DEFAULT_TTS_PROVIDERS,
    resolve_api_key,
    resolve_theme_name,
)
from vibe.core.config.models import (
    ConnectorConfig,
    ExperimentsConfig,
    MCPServer,
    MissingAPIKeyError,
    ModelConfig,
    ProjectContextConfig,
    ProviderConfig,
    SessionLoggingConfig,
    TranscribeModelConfig,
    TranscribeProviderConfig,
    TTSModelConfig,
    TTSProviderConfig,
)
from vibe.core.config.schema import (
    ConfigSchema,
    WithConcatMerge,
    WithReplaceMerge,
    WithShallowMerge,
    WithUnionMerge,
)
from vibe.core.prompts import (
    SystemPrompt,
    UtilityPrompt,
    load_prompt,
    load_system_prompt,
)


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


def _expand_paths(v: Any) -> list[Path]:
    if not v:
        return []
    return [Path(p).expanduser().resolve() for p in v]


def _normalize_tool_configs(v: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(v, dict):
        return {}
    return {name: cfg if isinstance(cfg, dict) else {} for name, cfg in v.items()}


class VibeConfigSchema(ConfigSchema):
    # Models
    active_model: Annotated[str, WithReplaceMerge()] = DEFAULT_ACTIVE_MODEL_CONFIG.alias
    providers: Annotated[list[ProviderConfig], WithUnionMerge(merge_key="name")] = (
        Field(default_factory=lambda: list(DEFAULT_PROVIDERS))
    )
    models: Annotated[
        list[ModelConfig],
        WithUnionMerge(merge_key="alias"),
        AfterValidator(_unique_by("alias")),
    ] = Field(default_factory=lambda: list(DEFAULT_MODELS))
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
        WithShallowMerge(),
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
    enabled_tools: Annotated[list[str], WithConcatMerge()] = Field(
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
            "A list of tool names/patterns to disable. Ignored if 'enabled_tools'"
            " is set. Supports glob patterns and regex with 're:' prefix."
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
        default=BuiltinAgentName.DEFAULT,
        description=(
            "Agent profile to use when no --agent flag is passed. "
            "Builtin: default, plan, accept-edits, auto-approve. "
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
    vibe_code_project_name: Annotated[str | None, WithReplaceMerge()] = None
    enable_otel: Annotated[bool, WithReplaceMerge()] = False
    otel_endpoint: Annotated[str, WithReplaceMerge()] = ""
    console_base_url: Annotated[str, WithReplaceMerge()] = DEFAULT_CONSOLE_BASE_URL
    enable_experimental_hooks: Annotated[bool, WithReplaceMerge()] = False

    # Top-level scalars
    theme: Annotated[str, WithReplaceMerge(), BeforeValidator(resolve_theme_name)] = (
        DEFAULT_THEME
    )
    experiment_overrides: Annotated[dict[str, str], WithReplaceMerge()] = Field(
        default_factory=dict
    )
    applied_migrations: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list
    )
    disable_welcome_banner_animation: Annotated[bool, WithReplaceMerge()] = False
    autocopy_to_clipboard: Annotated[bool, WithReplaceMerge()] = True
    file_watcher_for_autocomplete: Annotated[bool, WithReplaceMerge()] = False
    ask_confirmation_on_exit: Annotated[bool, WithReplaceMerge()] = True
    displayed_workdir: Annotated[str, WithReplaceMerge()] = ""
    context_warnings: Annotated[bool, WithReplaceMerge()] = False
    voice_mode_enabled: Annotated[bool, WithReplaceMerge()] = False
    narrator_enabled: Annotated[bool, WithReplaceMerge()] = False
    bypass_tool_permissions: Annotated[bool, WithReplaceMerge()] = False
    raise_on_compaction_failure: Annotated[bool, WithReplaceMerge()] = False
    enable_telemetry: Annotated[bool, WithReplaceMerge()] = True
    system_prompt_id: Annotated[str, WithReplaceMerge()] = SystemPrompt.CLI
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

    def get_active_model(self) -> ModelConfig:
        if model := next(
            (m for m in self.models if m.alias == self.active_model), None
        ):
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
    def _apply_global_auto_compact_threshold(self) -> VibeConfigSchema:
        models = [
            model
            if "auto_compact_threshold" in model.model_fields_set
            else model.model_copy(
                update={"auto_compact_threshold": self.auto_compact_threshold}
            )
            for model in self.models
        ]
        object.__setattr__(self, "models", models)
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
    def _check_api_key(self) -> VibeConfigSchema:
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
