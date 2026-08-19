from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
from pathlib import Path
from typing import Literal, Protocol, TypedDict

from pydantic import JsonValue

from vibe.app_server._shell import restored_shell_effect_state, shell_effect_detail
from vibe.app_server._tool_projection import (
    project_effect_detail,
    project_effect_output_value,
)
from vibe.app_server._utils import now_ms
from vibe.app_server._worktree_effects import WorktreeEffect
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
    CancelledEffectState,
    CompletedEffectState,
    ConfigIssue,
    ConnectorCounts,
    ContentBlock,
    DebugLogEntry,
    DebugLogPage,
    EffectCallDisplay,
    EffectResultDisplay,
    EffectState,
    FailedEffectState,
    GenericEffectDetail,
    ImageAttachment,
    ImageContentBlock,
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
    MCPToolSummary,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicError,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicReasoningEntry,
    ResourceContentBlock,
    SessionLogSummary,
    SkillSummary,
    SubagentEffectDetail,
    TextContentBlock,
    ToolSummary,
)
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents import AgentProfile
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
from vibe.core.log_reader import PaginatedLogs
from vibe.core.tools.connectors.connector_registry import ConnectorAuthAction
from vibe.core.tools.connectors.counts import compute_connector_counts
from vibe.core.tools.remote import AuthStatus, MCPTool
from vibe.core.types import (
    ImageAttachment as CoreImageAttachment,
    LLMMessage,
    Role,
    SessionMetadata,
    WorktreeContext,
)
from vibe.core.utils import CANCELLATION_TAG, TOOL_ERROR_TAG, TaggedText
from vibe.user_content import UserResource
from vibe.utils.tool_presentation import ToolCallPresentation


def project_config(agent_loop: AgentLoop) -> ConfigView:
    return project_config_view(
        agent_loop.config,
        active_model_pinned=bool(
            agent_loop.config_orchestrator.persisted_active_model()
        ),
        awaiting_experiment_model=agent_loop.awaiting_experiment_model,
    )


def project_config_view(
    config: VibeConfigSchema,
    *,
    active_model_pinned: bool = False,
    awaiting_experiment_model: bool = False,
) -> ConfigView:
    transcribe_model = config.get_active_transcribe_model()
    tts_model = config.get_active_tts_model()
    default_model_alias = (
        config.resolve_default_model_alias()
        if active_model_pinned
        else config.get_active_model().alias
    )
    return ConfigView(
        active_model=_project_model_config(config.get_active_model()),
        active_model_pinned=active_model_pinned,
        awaiting_experiment_model=awaiting_experiment_model,
        default_model_alias=default_model_alias,
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
        enable_update_checks=config.enable_update_checks,
        enable_notifications=config.enable_notifications,
        vibe_code_enabled=config.vibe_code_enabled,
        models=[
            _project_model_config(model) for model in config.available_models().values()
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


def _project_model_config(model: ModelConfig) -> ModelConfigView:
    return ModelConfigView(
        name=model.name,
        alias=model.alias,
        thinking=model.thinking,
        supports_images=model.supports_images,
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
    return _project_agent(active), [_project_agent(profile) for profile in available]


def project_agents(agent_loop: AgentLoop) -> tuple[AgentSummary, list[AgentSummary]]:
    return project_agent_summaries(
        agent_loop.agent_profile, agent_loop.agent_manager.available_agents.values()
    )


def project_skills(agent_loop: AgentLoop) -> list[SkillSummary]:
    return [
        SkillSummary.model_validate({
            "name": skill.name,
            "description": skill.description,
            "prompt": skill.prompt,
            "user_invocable": skill.user_invocable,
            "source": skill.source.value,
        })
        for skill in agent_loop.skill_manager.available_skills.values()
    ]


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
    return MCPState(
        sources=[
            *_project_mcp_servers(agent_loop, tools, discovery_errors_set),
            *_project_mcp_connectors(agent_loop, tools),
        ],
        discovery_errors=dict(discovery_errors or {}),
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
        description = (
            (tool_class.description or "")
            .removeprefix(f"[{source_name}] ")
            .split("\n")[0]
        )
        tools.setdefault((kind, source_name), []).append(
            MCPToolSummary(
                name=tool_class.get_remote_name(),
                description=description,
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
        sources.append(
            MCPSourceSummary(
                name=name,
                kind=MCPSourceKind.CONNECTOR,
                transport="connector",
                status=status,
                tools=sorted(
                    tools.get((MCPSourceKind.CONNECTOR, name), []),
                    key=lambda tool: tool.name,
                ),
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


def project_message_content(
    text: str | None,
    images: Sequence[CoreImageAttachment] | None,
    resources: Sequence[UserResource] | None = None,
) -> list[ContentBlock]:
    content: list[ContentBlock] = []
    if text:
        content.append(TextContentBlock(text=text))
    content.extend(
        ImageContentBlock(
            attachment=ImageAttachment.model_validate(image.model_dump(mode="json"))
        )
        for image in images or []
    )
    content.extend(
        ResourceContentBlock(resource=resource) for resource in resources or []
    )
    return content


def project_message_history(
    session_id: str, messages: Sequence[LLMMessage], metadata: SessionMetadata | None
) -> list[PublicHistoryEntry]:
    timestamp = now_ms()
    entries: list[PublicHistoryEntry] = []
    effect_indices: dict[str, int] = {}
    child_sessions = (
        {link.tool_call_id: link.session_id for link in metadata.child_sessions}
        if metadata is not None
        else {}
    )
    # First: the worktree is created before the session has a single message.
    if metadata is not None and metadata.created_worktree is not None:
        entries.append(_history_worktree(session_id, metadata.created_worktree))
    for index, message in enumerate(messages):
        _project_stored_message(
            session_id,
            message,
            index,
            timestamp + index,
            entries,
            effect_indices,
            child_sessions,
        )
    return entries


def _project_stored_message(
    session_id: str,
    message: LLMMessage,
    index: int,
    created_at: int,
    entries: list[PublicHistoryEntry],
    effect_indices: dict[str, int],
    child_sessions: dict[str, str],
) -> None:
    if message.role is Role.system:
        return
    if message.context_boundary == "compaction":
        _append_compaction_history(session_id, message, index, created_at, entries)
        return
    if message.injected:
        _append_injected_history(session_id, message, entries)
        return
    match message.role:
        case Role.user:
            entries.append(
                _history_user_message(session_id, message, index, created_at)
            )
        case Role.assistant:
            _append_assistant_history(
                session_id,
                message,
                index,
                created_at,
                entries,
                effect_indices,
                child_sessions,
            )
        case Role.tool:
            _apply_tool_history(
                session_id,
                message,
                index,
                created_at,
                entries,
                effect_indices,
                child_sessions,
            )


def _history_worktree(session_id: str, worktree: WorktreeContext) -> PublicEffectEntry:
    restored = WorktreeEffect.restored(worktree)
    return PublicEffectEntry(
        **_history_fields(session_id, worktree.entry_id, worktree.created_at),
        title="worktree",
        detail=restored.detail,
        state=restored.state,
    )


def _append_injected_history(
    session_id: str, message: LLMMessage, entries: list[PublicHistoryEntry]
) -> None:
    shell = message.manual_shell
    if shell is None:
        return
    entries.append(
        PublicEffectEntry(
            **_history_fields(session_id, shell.operation_id, shell.created_at),
            title="shell",
            detail=shell_effect_detail(shell.command),
            state=restored_shell_effect_state(shell),
        )
    )


def _append_compaction_history(
    session_id: str,
    message: LLMMessage,
    index: int,
    created_at: int,
    entries: list[PublicHistoryEntry],
) -> None:
    message_id = message.message_id or f"history:{index}:compaction"
    entries.append(
        PublicCheckpointEntry(
            **_history_fields(
                session_id, f"checkpoint:compaction:{message_id}", created_at
            ),
            kind="compaction",
            message="Context compacted",
            details={},
        )
    )


def _history_user_message(
    session_id: str, message: LLMMessage, index: int, created_at: int
) -> PublicMessageEntry:
    return PublicMessageEntry(
        **_history_fields(session_id, history_message_id(message, index), created_at),
        role="user",
        content=project_message_content(
            message.input_text if message.input_text is not None else message.content,
            message.images,
            message.resources,
        ),
        source="harness",
        user_display_content=message.user_display_content,
    )


def _append_assistant_history(
    session_id: str,
    message: LLMMessage,
    index: int,
    created_at: int,
    entries: list[PublicHistoryEntry],
    effect_indices: dict[str, int],
    child_sessions: dict[str, str],
) -> None:
    if message.reasoning_content:
        entries.append(
            PublicReasoningEntry(
                **_history_fields(
                    session_id,
                    message.reasoning_message_id or f"history:{index}:reasoning",
                    created_at,
                ),
                text=message.reasoning_content,
            )
        )
    if message.content:
        entries.append(
            PublicMessageEntry(
                **_history_fields(
                    session_id,
                    message.message_id or f"history:{index}:assistant",
                    created_at,
                ),
                role="assistant",
                content=[TextContentBlock(text=message.content)],
                source="harness",
            )
        )
    for tool_call in message.tool_calls or []:
        tool_call_id = tool_call.id or f"history:{index}:effect"
        effect_indices[tool_call_id] = len(entries)
        entries.append(
            _history_effect(
                session_id,
                tool_call_id,
                tool_call.function.name or "unknown",
                tool_call.function.arguments,
                created_at,
                presentation=tool_call.presentation,
                child_session_id=child_sessions.get(tool_call_id),
            )
        )


def _apply_tool_history(
    session_id: str,
    message: LLMMessage,
    index: int,
    created_at: int,
    entries: list[PublicHistoryEntry],
    effect_indices: dict[str, int],
    child_sessions: dict[str, str],
) -> None:
    tool_call_id = message.tool_call_id or ""
    effect_index = effect_indices.get(tool_call_id)
    if effect_index is None:
        entries.append(
            _history_effect(
                session_id,
                tool_call_id or f"history:{index}:effect",
                message.name or "tool",
                None,
                created_at,
                result_message=message,
                child_session_id=child_sessions.get(tool_call_id),
            )
        )
        return
    effect = entries[effect_index]
    if not isinstance(effect, PublicEffectEntry):
        return
    entries[effect_index] = effect.model_copy(
        update={"state": _persisted_effect_state(effect, message)}
    )


def history_message_id(message: LLMMessage, index: int) -> str:
    return message.message_id or f"history:{index}:{message.role.value}"


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


def _project_agent(profile: AgentProfile) -> AgentSummary:
    return AgentSummary(
        name=profile.name,
        display_name=profile.display_name,
        description=profile.description,
        safety=profile.safety,
        agent_type=profile.agent_type,
    )


def _project_issue(issue: _ConfigIssue) -> ConfigIssue:
    return ConfigIssue(file=str(issue.file), message=issue.message)


class _HistoryFields(TypedDict):
    id: str
    session_id: str
    turn_id: str | None
    created_at: int
    updated_at: int
    generation_status: PublicEntryGenerationStatus
    related_entry_id: str | None


def _history_fields(session_id: str, entry_id: str, created_at: int) -> _HistoryFields:
    return {
        "id": entry_id,
        "session_id": session_id,
        "turn_id": None,
        "created_at": created_at,
        "updated_at": created_at,
        "generation_status": PublicEntryGenerationStatus.COMPLETED,
        "related_entry_id": None,
    }


def _history_effect(
    session_id: str,
    entry_id: str,
    tool_name: str,
    raw_arguments: str | None,
    created_at: int,
    *,
    result_message: LLMMessage | None = None,
    presentation: ToolCallPresentation | None = None,
    child_session_id: str | None = None,
) -> PublicEffectEntry:
    arguments = _parse_arguments(raw_arguments)
    if presentation is not None:
        detail = project_effect_detail(tool_name, arguments, presentation)
    else:
        summary = _generic_call_summary(tool_name, arguments)
        display = EffectCallDisplay(
            summary=summary,
            verb="Running",
            message=summary,
            settled_verb="Ran",
            settled_message=summary,
            status_text=f"Running {tool_name}",
        )
        detail = GenericEffectDetail(
            tool_name=tool_name, input=arguments, display=display
        )
    if isinstance(detail, SubagentEffectDetail):
        detail = detail.model_copy(update={"child_session_id": child_session_id})
    entry = PublicEffectEntry(
        **_history_fields(session_id, entry_id, created_at),
        title=tool_name,
        detail=detail,
        state=CompletedEffectState(
            output=None,
            output_text="",
            display=EffectResultDisplay(success=True, message=f"{tool_name} completed"),
        ),
    )
    return entry.model_copy(
        update={"state": _persisted_effect_state(entry, result_message)}
    )


def _persisted_effect_state(
    effect: PublicEffectEntry, message: LLMMessage | None
) -> EffectState:
    if message is None:
        reason = "Tool did not complete before the session ended"
        return CancelledEffectState(
            reason=reason,
            output_text="",
            display=EffectResultDisplay(success=False, message=reason),
        )
    text = TaggedText.from_string(message.content or "")
    display_message = text.message or f"{effect.detail.tool_name} completed"
    if text.tag == CANCELLATION_TAG:
        return CancelledEffectState(
            reason=display_message,
            output_text=text.message,
            display=EffectResultDisplay(success=False, message=display_message),
        )
    if text.tag == TOOL_ERROR_TAG:
        return FailedEffectState(
            error=PublicError(message=display_message),
            output_text=text.message,
            display=EffectResultDisplay(success=False, message=display_message),
        )
    persisted = message.tool_result
    if persisted is not None:
        presentation = persisted.presentation
        display = (
            presentation.display
            if presentation is not None
            else EffectResultDisplay(success=True, message=display_message)
        )
        duration_ms = (persisted.duration or 0.0) * 1000
        if persisted.cancelled:
            return CancelledEffectState(
                reason=display.message,
                output_text=text.message,
                duration_ms=duration_ms,
                display=display,
            )
        kind = presentation.kind if presentation is not None else effect.detail.kind
        output = (
            presentation.projected_output
            if presentation is not None and presentation.projected_output is not None
            else persisted.output
        )
        return CompletedEffectState(
            output=project_effect_output_value(kind, output),
            output_text=text.message,
            duration_ms=duration_ms,
            display=display,
        )
    return CompletedEffectState(
        output=None,
        output_text=text.message,
        display=EffectResultDisplay(success=True, message=display_message),
    )


def _parse_arguments(value: str | None) -> JsonValue:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    return parsed


def _generic_call_summary(tool_name: str, arguments: JsonValue) -> str:
    if not isinstance(arguments, dict):
        return tool_name
    rendered = ", ".join(
        f"{key}={value!r}" for key, value in list(arguments.items())[:3]
    )
    return f"{tool_name}({rendered})"
