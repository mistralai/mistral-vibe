from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import hashlib
from pathlib import Path
from typing import Literal, Protocol

from vibe.app_server._history_projection import (
    history_message_id,
    project_message_history,
)
from vibe.app_server._session_model import active_model_is_pinned
from vibe.app_server.config import (
    AudioProviderView,
    ConfigView,
    ModelConfigView,
    SpeechConfigView,
    TranscribeModelConfigView,
    TranscriptionConfigView,
    TTSModelConfigView,
)
from vibe.app_server.models import (
    AgentStatsSnapshot,
    AgentSummary,
    ConfigIssue,
    ConnectorCounts,
    DebugLogEntry,
    DebugLogPage,
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
    MCPToolSummary,
    PublicHistoryEntry,
    SessionLogSummary,
    SkillSummary,
    ToolSummary,
)
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents import AgentProfile, BuiltinAgentName
from vibe.core.config import (
    ModelConfig,
    TranscribeClient,
    TranscribeModelConfig,
    TranscribeProviderConfig,
    TTSClient,
    TTSModelConfig,
    TTSProviderConfig,
    VibeConfigSchema,
)
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.log_reader import PaginatedLogs
from vibe.core.skills.models import SkillInfo, SkillSource
from vibe.core.tools.connectors.connector_registry import ConnectorAuthAction
from vibe.core.tools.connectors.counts import compute_connector_counts
from vibe.core.tools.remote import AuthStatus, MCPTool
from vibe.core.types import Role
from vibe.core.utils import name_matches
from vibe.utils.mcp import format_tool_display_description


def project_config(agent_loop: AgentLoop) -> ConfigView:
    return project_config_view(
        agent_loop.config,
        active_model_pinned=active_model_is_pinned(agent_loop.config_orchestrator),
        awaiting_experiment_model=agent_loop.awaiting_experiment_model,
    )


def project_config_view(
    config: VibeConfigSchema,
    *,
    active_model_pinned: bool = False,
    awaiting_experiment_model: bool = False,
    # Whether the caller's backend can show a blind model a description in
    # place of the pixels. Only the Unified one can, so the legacy projection
    # keeps reporting the active model's own vision.
    image_fallback: bool = False,
) -> ConfigView:
    transcribe_model = config.get_active_transcribe_model()
    tts_model = config.get_active_tts_model()
    active_model = config.get_active_model()
    # A describer, not Core's resource-link projection, is what makes the
    # promise good for every source kind: the link only exists for a
    # file-backed image, so an inline one would still reach a blind model.
    describable = image_fallback and config.get_vision_fallback_model() is not None
    return ConfigView(
        active_model=_project_model_config(active_model, image_fallback=image_fallback),
        active_model_pinned=active_model_pinned,
        images_supported=active_model.supports_images or describable,
        awaiting_experiment_model=awaiting_experiment_model,
        # The configured default, never the active model: clients render it as
        # the "Default (currently X)" hint, which must stay stable while a pin
        # is in effect.
        default_model_alias=config.resolve_default_model_alias(),
        default_agent=config.default_agent,
        theme=config.theme,
        log_level=config.log_level,
        disable_welcome_banner_animation=config.disable_welcome_banner_animation,
        show_greeting=config.show_greeting,
        autocopy_to_clipboard=config.autocopy_to_clipboard,
        file_watcher_for_autocomplete=config.file_watcher_for_autocomplete,
        ask_confirmation_on_exit=config.ask_confirmation_on_exit,
        voice_mode_enabled=config.voice_mode_enabled,
        narrator_enabled=config.narrator_enabled,
        show_thinking_nodes=config.show_thinking_nodes,
        show_subagent_status_list=config.show_subagent_status_list,
        worktree_limit=config.worktree_limit,
        enable_update_checks=config.enable_update_checks,
        enable_notifications=config.enable_notifications,
        enable_system_trust_store=config.enable_system_trust_store,
        experimental_enable_tab_status=config.experimental_enable_tab_status,
        enable_telemetry=config.enable_telemetry,
        experimental_enable_registry_skills=config.experimental_enable_registry_skills,
        models=[
            _project_model_config(model, image_fallback=image_fallback)
            for model in config.available_models().values()
        ],
        transcribe_models=[model.alias for model in config.transcribe_models],
        tts_models=[model.alias for model in config.tts_models],
        transcription=TranscriptionConfigView(
            model=_project_transcribe_model(transcribe_model),
            provider=_project_transcribe_provider(
                config.get_transcribe_provider_for_model(transcribe_model)
            ),
        ),
        speech=SpeechConfigView(
            model=_project_tts_model(tts_model),
            provider=_project_tts_provider(
                config.get_tts_provider_for_model(tts_model)
            ),
        ),
        validation_warnings=list(config.validation_warnings),
    )


def project_workdir(agent_loop: AgentLoop) -> str:
    return agent_loop.config.displayed_workdir or str(agent_loop.cwd)


def _project_audio_client(client: TranscribeClient | TTSClient) -> Literal["mistral"]:
    match client:
        case TranscribeClient.MISTRAL | TTSClient.MISTRAL:
            return "mistral"
    raise ValueError(f"Unsupported audio client: {client}")


def _project_model_config(
    model: ModelConfig, *, image_fallback: bool
) -> ModelConfigView:
    image_delivery: Literal["native", "resource_link"] | None = None
    if model.supports_images:
        image_delivery = "native"
    elif image_fallback:
        image_delivery = "resource_link"
    return ModelConfigView(
        name=model.name,
        alias=model.alias,
        thinking=model.thinking,
        supports_images=model.supports_images,
        display_name=model.display_name or model.alias,
        max_context_length=model.max_context_length,
        image_delivery=image_delivery,
        thinking_levels=list(model.thinking_levels),
    )


def _project_transcribe_model(
    model: TranscribeModelConfig,
) -> TranscribeModelConfigView:
    return TranscribeModelConfigView(
        name=model.name,
        sample_rate=model.sample_rate,
        encoding=model.encoding,
        language=model.language,
        target_streaming_delay_ms=model.target_streaming_delay_ms,
    )


def _project_transcribe_provider(
    provider: TranscribeProviderConfig,
) -> AudioProviderView:
    return AudioProviderView(
        api_base=provider.api_base,
        api_key_env_var=provider.api_key_env_var,
        client=_project_audio_client(provider.client),
    )


def _project_tts_model(model: TTSModelConfig) -> TTSModelConfigView:
    return TTSModelConfigView(
        name=model.name, voice=model.voice, response_format=model.response_format
    )


def _project_tts_provider(provider: TTSProviderConfig) -> AudioProviderView:
    return AudioProviderView(
        api_base=provider.api_base,
        api_key_env_var=provider.api_key_env_var,
        client=_project_audio_client(provider.client),
    )


def project_stats(agent_loop: AgentLoop) -> AgentStatsSnapshot:
    stats = agent_loop.stats
    return AgentStatsSnapshot(
        steps=stats.steps,
        session_prompt_tokens=stats.session_prompt_tokens,
        session_completion_tokens=stats.session_completion_tokens,
        session_cached_tokens=stats.session_cached_tokens,
        input_price_per_million=stats.input_price_per_million,
        output_price_per_million=stats.output_price_per_million,
        cached_input_price_per_million=stats.cached_input_price_per_million,
        tool_calls_agreed=stats.tool_calls_agreed,
        tool_calls_rejected=stats.tool_calls_rejected,
        tool_calls_failed=stats.tool_calls_failed,
        tool_calls_succeeded=stats.tool_calls_succeeded,
        context_tokens=stats.context_tokens,
        last_turn_prompt_tokens=stats.last_turn_prompt_tokens,
        last_turn_completion_tokens=stats.last_turn_completion_tokens,
        last_turn_cached_tokens=stats.last_turn_cached_tokens,
        last_turn_duration=stats.last_turn_duration,
        tokens_per_second=stats.tokens_per_second,
    )


def project_agent_summaries(
    active: AgentProfile, available: Iterable[AgentProfile]
) -> tuple[AgentSummary, list[AgentSummary]]:
    return project_agent_summary(active), [
        project_agent_summary(profile) for profile in available
    ]


# Modes hidden from the Unified Harness mode picker/cycle. They stay selectable
# (resume, pinned running mode, explicit ``--agent``/switch); only the list the
# client offers is trimmed.
_UNIFIED_HIDDEN_PICKER_AGENTS: frozenset[str] = frozenset({BuiltinAgentName.PLAN})


def project_unified_agent_summaries(
    active: AgentProfile, available: Iterable[AgentProfile]
) -> tuple[AgentSummary, list[AgentSummary]]:
    """Project agents for a Unified Harness client, hiding plan mode from the picker.

    The active profile is always projected, so a session already running a hidden
    mode still reports it; only the selectable list drops the hidden modes.
    """
    visible = [
        profile
        for profile in available
        if profile.name not in _UNIFIED_HIDDEN_PICKER_AGENTS
    ]
    return project_agent_summaries(active, visible)


def project_agents(agent_loop: AgentLoop) -> tuple[AgentSummary, list[AgentSummary]]:
    return project_agent_summaries(
        agent_loop.agent_profile, agent_loop.agent_manager.available_agents.values()
    )


def project_skill_summaries(skills: Iterable[SkillInfo]) -> list[SkillSummary]:
    return [_skill_summary(skill) for skill in skills]


def _skill_summary(
    skill: SkillInfo, enabled: bool = True, locked: bool = False
) -> SkillSummary:
    return SkillSummary.model_validate({
        "name": skill.name,
        "description": skill.description,
        "prompt": skill.prompt,
        "user_invocable": skill.user_invocable,
        "source": skill.source.value,
        "scope": skill.scope.value,
        "registry": skill.registry.model_dump() if skill.registry else None,
        "enabled": enabled,
        "locked": locked,
    })


def project_skills(agent_loop: AgentLoop) -> list[SkillSummary]:
    return project_skill_summaries(agent_loop.skill_manager.available_skills.values())


def writable_disabled_skills(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> Sequence[str] | None:
    """The writable layer's own ``disabled_skills``, or None when unavailable.

    ``disabled_skills`` concat-merges, so the effective list is every layer at
    once while a toggle can only add to or remove from the writable one. Reads
    the cached layer rather than loading, to stay usable from sync projection.
    """
    try:
        layer = orchestrator.get_layer(orchestrator.writable_layer_name)
    except KeyError:
        return None
    data = layer.cached_data
    if data is None:
        return None
    names = getattr(data, "disabled_skills", None)
    return list(names) if names else []


def project_installed_skill_summaries(
    skills: Iterable[SkillInfo],
    config: VibeConfigSchema,
    user_disabled: Sequence[str] | None = None,
) -> list[SkillSummary]:
    """Browser rows for *skills*, marked against the config's skill filters.

    ``enabled`` is whether the agent loads the skill. ``locked`` is whether the
    browser can change that, and the two are independent: a skill can be
    enabled and locked, or disabled and locked.

    ``user_disabled`` is the writable layer's own ``disabled_skills``.
    ``disabled_skills`` concatenates across layers, so a name a project or admin
    layer disabled cannot be re-enabled by rewriting the writable one; without
    it a row offers a toggle that silently does nothing. Omitting the argument
    assumes a single layer.

    Plugin skills are locked: they come from an installed plugin and are managed
    through ``/plugins``, not by this config.
    """
    allowed = config.enabled_skills
    disabled = config.disabled_skills
    own = list(disabled if user_disabled is None else user_disabled)

    def _enabled(info: SkillInfo) -> bool:
        if info.source is SkillSource.PLUGIN:
            return True
        if allowed:
            return name_matches(info.name, allowed)
        return not (disabled and name_matches(info.name, disabled))

    def _locked(info: SkillInfo) -> bool:
        if info.source is SkillSource.PLUGIN:
            return True
        if allowed:
            return True
        if not disabled:
            return False
        patterns = [pattern for pattern in disabled if pattern != info.name]
        if name_matches(info.name, patterns):
            return True
        return name_matches(info.name, disabled) and info.name not in own

    return [_skill_summary(info, _enabled(info), _locked(info)) for info in skills]


def project_installed_skills(agent_loop: AgentLoop) -> list[SkillSummary]:
    """One row per installed skill, mirroring the agent's resolved state.

    ``installed_skills`` is the deduped, project/source-wins set the agent loads
    (builtins excluded) *including* disabled skills, so a skill turned off stays
    in the list marked not-enabled and can be turned back on; enabled rows are
    exactly what the agent uses.
    """
    return project_installed_skill_summaries(
        agent_loop.skill_manager.installed_skills(),
        agent_loop.config,
        writable_disabled_skills(agent_loop.config_orchestrator),
    )


def project_tools(agent_loop: AgentLoop) -> list[ToolSummary]:
    custom_tool_names = agent_loop.tool_manager.custom_tool_names
    return [
        ToolSummary(name=name, is_custom=name in custom_tool_names)
        for name in agent_loop.tool_manager.available_tools
    ]


def project_connectors(agent_loop: AgentLoop) -> ConnectorCounts:
    connected, total = compute_connector_counts(
        agent_loop.config, agent_loop.connector_registry
    )
    return ConnectorCounts(connected=connected, total=total)


def project_mcp(
    agent_loop: AgentLoop, *, discovery_errors: Mapping[str, str] | None = None
) -> MCPState:
    tools = _project_mcp_tools(agent_loop)
    discovery_errors_set = set(discovery_errors) if discovery_errors else set()
    connector_registry = agent_loop.connector_registry
    connector_error = (
        connector_registry.bootstrap_error() if connector_registry is not None else None
    )
    return MCPState(
        sources=[
            *_project_mcp_servers(agent_loop, tools, discovery_errors_set),
            *_project_mcp_connectors(agent_loop, tools),
        ],
        discovery_errors=dict(discovery_errors or {}),
        connector_error=connector_error,
    )


def _project_mcp_tools(
    agent_loop: AgentLoop,
) -> dict[tuple[MCPSourceKind, str], list[MCPToolSummary]]:
    tools: dict[tuple[MCPSourceKind, str], list[MCPToolSummary]] = {}
    available = agent_loop.tool_manager.available_tools
    for tool_name, tool_class in agent_loop.tool_manager.registered_tools.items():
        if not issubclass(tool_class, MCPTool):
            continue
        source_name = tool_class.get_server_name()
        if source_name is None:
            continue
        kind = (
            MCPSourceKind.CONNECTOR
            if tool_class.is_connector()
            else MCPSourceKind.SERVER
        )
        tools.setdefault((kind, source_name), []).append(
            MCPToolSummary(
                name=tool_class.get_remote_name(),
                description=format_tool_display_description(
                    tool_class.description, source_name=source_name
                ),
                enabled=tool_name in available,
            )
        )
    return tools


def _project_mcp_servers(
    agent_loop: AgentLoop,
    tools: dict[tuple[MCPSourceKind, str], list[MCPToolSummary]],
    discovery_errors: set[str],
) -> list[MCPSourceSummary]:
    registry = agent_loop.mcp_registry
    server_statuses = registry.status() if registry is not None else {}
    sources: list[MCPSourceSummary] = []
    for server in agent_loop.config.mcp_servers:
        if server.disabled:
            status = MCPSourceStatus.DISABLED
        elif server.name in discovery_errors:
            status = MCPSourceStatus.UNAVAILABLE
        else:
            match server_statuses.get(server.name):
                case AuthStatus.NEEDS_AUTH:
                    status = MCPSourceStatus.NEEDS_AUTH
                case AuthStatus.OK:
                    status = MCPSourceStatus.CONNECTED
                case _:
                    status = MCPSourceStatus.ENABLED
        sources.append(
            MCPSourceSummary(
                name=server.name,
                kind=MCPSourceKind.SERVER,
                transport=server.transport,
                status=status,
                tools=sorted(
                    tools.get((MCPSourceKind.SERVER, server.name), []),
                    key=lambda tool: tool.name,
                ),
            )
        )
    return sources


def _project_mcp_connectors(
    agent_loop: AgentLoop, tools: dict[tuple[MCPSourceKind, str], list[MCPToolSummary]]
) -> list[MCPSourceSummary]:
    connector_registry = agent_loop.connector_registry
    connector_configs = {
        connector.name: connector for connector in agent_loop.config.connectors
    }
    connector_names = set(connector_configs)
    if connector_registry is not None:
        connector_names.update(connector_registry.get_connector_names())
    sources: list[MCPSourceSummary] = []
    for name in sorted(connector_names):
        config = connector_configs.get(name)
        disabled = config is None or config.disabled
        if disabled:
            status = MCPSourceStatus.DISABLED
        elif connector_registry is None:
            status = MCPSourceStatus.UNAVAILABLE
        elif connector_registry.is_connected(name):
            status = MCPSourceStatus.CONNECTED
        else:
            match connector_registry.get_auth_action(name):
                case ConnectorAuthAction.OAUTH:
                    status = MCPSourceStatus.NEEDS_AUTH
                case ConnectorAuthAction.CREDENTIALS_SETUP:
                    status = MCPSourceStatus.NEEDS_SETUP
                case _:
                    status = MCPSourceStatus.UNAVAILABLE
        error = (
            connector_registry.connector_error_for(name)
            if connector_registry is not None
            else None
        )
        source_tools = {
            tool.name: tool for tool in tools.get((MCPSourceKind.CONNECTOR, name), [])
        }
        if connector_registry is not None:
            for descriptor in connector_registry.get_catalog_tools(name):
                source_tools.setdefault(
                    descriptor.name,
                    MCPToolSummary(
                        name=descriptor.name,
                        description=descriptor.description or "",
                        enabled=False,
                    ),
                )
        sources.append(
            MCPSourceSummary(
                name=name,
                kind=MCPSourceKind.CONNECTOR,
                transport="connector",
                status=status,
                tools=sorted(source_tools.values(), key=lambda tool: tool.name),
                error=error,
            )
        )
    return sources


def project_session_log(agent_loop: AgentLoop) -> SessionLogSummary:
    session_logger = agent_loop.session_logger
    session_dir = session_logger.session_dir if session_logger.persisted else None
    return SessionLogSummary(
        enabled=session_logger.enabled,
        session_id=session_logger.session_id,
        persisted=session_logger.persisted,
        path=str(session_dir) if session_dir is not None else None,
        title=session_logger.title,
        needs_initial_auto_title=session_logger.needs_initial_auto_title(),
    )


def project_diagnostics(agent_loop: AgentLoop) -> tuple[list[ConfigIssue], int]:
    issues = [
        *(_project_issue(issue) for issue in agent_loop.hook_config_issues),
        *(_project_issue(issue) for issue in agent_loop.skill_manager.config_issues),
    ]
    return issues, agent_loop.hooks_count


def project_debug_logs(logs: PaginatedLogs) -> DebugLogPage:
    return DebugLogPage(
        entries=[
            DebugLogEntry(
                id=hashlib.sha1(
                    entry.raw_line.encode("utf-8"), usedforsecurity=False
                ).hexdigest(),
                timestamp=entry.timestamp,
                ppid=entry.ppid,
                pid=entry.pid,
                level=entry.level,
                message=entry.message,
                raw_line=entry.raw_line,
            )
            for entry in logs.entries
        ],
        has_more=logs.has_more,
        cursor=logs.cursor,
    )


def project_history(agent_loop: AgentLoop) -> list[PublicHistoryEntry]:
    return project_message_history(
        agent_loop.session_id,
        agent_loop.messages,
        agent_loop.session_logger.session_metadata,
    )


def history_user_message_index(agent_loop: AgentLoop, entry_id: str) -> int | None:
    for index, message in enumerate(agent_loop.messages):
        if message.role is not Role.user or message.injected:
            continue
        if history_message_id(message, index) == entry_id:
            return index
    return None


class _ConfigIssue(Protocol):
    file: Path
    message: str


def project_agent_summary(profile: AgentProfile) -> AgentSummary:
    return AgentSummary(
        name=profile.name,
        display_name=profile.display_name,
        description=profile.description,
        safety=profile.safety,
        agent_type=profile.agent_type,
    )


def _project_issue(issue: _ConfigIssue) -> ConfigIssue:
    return ConfigIssue(file=str(issue.file), message=issue.message)
