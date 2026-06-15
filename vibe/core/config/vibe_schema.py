from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config._settings import (
    DEFAULT_ACTIVE_MODEL_CONFIG,
    DEFAULT_ACTIVE_TRANSCRIBE_MODEL_CONFIG,
    DEFAULT_ACTIVE_TTS_MODEL_CONFIG,
    DEFAULT_API_TIMEOUT,
    DEFAULT_AUTO_COMPACT_THRESHOLD,
    DEFAULT_CONSOLE_BASE_URL,
    DEFAULT_MISTRAL_API_ENV_KEY,
    DEFAULT_MISTRAL_SERVER_URL,
    DEFAULT_MODELS,
    DEFAULT_PROVIDERS,
    DEFAULT_THEME,
    DEFAULT_TRANSCRIBE_MODELS,
    DEFAULT_TRANSCRIBE_PROVIDERS,
    DEFAULT_TTS_MODELS,
    DEFAULT_TTS_PROVIDERS,
    DEFAULT_VIBE_BASE_URL,
    DEFAULT_VIBE_CODE_TASK_QUEUE,
    DEFAULT_VIBE_CODE_WORKFLOW_ID,
    ConnectorConfig,
    ExperimentsConfig,
    MCPServer,
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
from vibe.core.prompts import SystemPrompt, UtilityPrompt


class VibeConfigSchema(ConfigSchema):
    # Models
    active_model: Annotated[str, WithReplaceMerge()] = DEFAULT_ACTIVE_MODEL_CONFIG.alias
    providers: Annotated[list[ProviderConfig], WithUnionMerge(merge_key="name")] = (
        Field(default_factory=lambda: list(DEFAULT_PROVIDERS))
    )
    models: Annotated[list[ModelConfig], WithUnionMerge(merge_key="alias")] = Field(
        default_factory=lambda: list(DEFAULT_MODELS)
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
        list[TranscribeModelConfig], WithUnionMerge(merge_key="alias")
    ] = Field(default_factory=lambda: list(DEFAULT_TRANSCRIBE_MODELS))
    active_tts_model: Annotated[str, WithReplaceMerge()] = (
        DEFAULT_ACTIVE_TTS_MODEL_CONFIG.alias
    )
    tts_providers: Annotated[
        list[TTSProviderConfig], WithUnionMerge(merge_key="name")
    ] = Field(default_factory=lambda: list(DEFAULT_TTS_PROVIDERS))
    tts_models: Annotated[list[TTSModelConfig], WithUnionMerge(merge_key="alias")] = (
        Field(default_factory=lambda: list(DEFAULT_TTS_MODELS))
    )

    # Tools
    tools: Annotated[dict[str, dict[str, Any]], WithShallowMerge()] = Field(
        default_factory=dict
    )
    tool_paths: Annotated[list[Path], WithConcatMerge()] = Field(
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
    mcp_servers: Annotated[list[MCPServer], WithUnionMerge(merge_key="name")] = Field(
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
    skill_paths: Annotated[list[Path], WithConcatMerge()] = Field(
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

    # Internal
    vibe_code_enabled: Annotated[bool, WithReplaceMerge()] = True
    vibe_code_base_url: Annotated[str, WithReplaceMerge()] = DEFAULT_MISTRAL_SERVER_URL
    vibe_code_workflow_id: Annotated[str, WithReplaceMerge()] = (
        DEFAULT_VIBE_CODE_WORKFLOW_ID
    )
    vibe_code_task_queue: Annotated[str | None, WithReplaceMerge()] = (
        DEFAULT_VIBE_CODE_TASK_QUEUE
    )
    vibe_code_api_key_env_var: Annotated[str, WithReplaceMerge()] = (
        DEFAULT_MISTRAL_API_ENV_KEY
    )
    vibe_code_project_name: Annotated[str | None, WithReplaceMerge()] = None
    vibe_code_experimental_nuage_enabled: Annotated[bool, WithReplaceMerge()] = False
    enable_otel: Annotated[bool, WithReplaceMerge()] = False
    otel_endpoint: Annotated[str, WithReplaceMerge()] = ""
    console_base_url: Annotated[str, WithReplaceMerge()] = DEFAULT_CONSOLE_BASE_URL
    enable_experimental_hooks: Annotated[bool, WithReplaceMerge()] = False

    # Top-level scalars
    theme: Annotated[str, WithReplaceMerge()] = DEFAULT_THEME
    experiment_overrides: Annotated[dict[str, str], WithReplaceMerge()] = Field(
        default_factory=dict
    )
    applied_migrations: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list
    )
    vim_keybindings: Annotated[bool, WithReplaceMerge()] = False
    disable_welcome_banner_animation: Annotated[bool, WithReplaceMerge()] = False
    autocopy_to_clipboard: Annotated[bool, WithReplaceMerge()] = True
    file_watcher_for_autocomplete: Annotated[bool, WithReplaceMerge()] = False
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
