from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import TYPE_CHECKING

from acp.helpers import SessionUpdate
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ContentToolCallContent,
    Implementation,
    PermissionOption,
    PermissionOptionKind,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionMode,
    SessionModeState,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
)

from vibe.acp.tools.session_update import resolve_kind, tool_call_session_update
from vibe.acp.user_display_content import USER_DISPLAY_CONTENT_META_KEY
from vibe.core.agents.models import AgentProfile, AgentType
from vibe.core.config.models import THINKING_LEVELS, ThinkingLevel
from vibe.core.llm.format import ResolvedToolCall
from vibe.core.proxy_setup import SUPPORTED_PROXY_VARS, get_current_proxy_settings
from vibe.core.tools.permissions import RequiredPermission
from vibe.core.types import (
    CompactEndEvent,
    CompactStartEvent,
    LLMMessage,
    ToolCall,
    ToolCallEvent,
)
from vibe.core.utils import compact_complete_display

if TYPE_CHECKING:
    from vibe.core.config import ModelConfig


class ToolOption(StrEnum):
    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    ALLOW_ALWAYS_PERMANENT = "allow_always_permanent"
    REJECT_ONCE = "reject_once"
    REJECT_ALWAYS = "reject_always"


_KIND_ALLOW_ONCE: PermissionOptionKind = "allow_once"
_KIND_ALLOW_ALWAYS: PermissionOptionKind = "allow_always"
_KIND_REJECT_ONCE: PermissionOptionKind = "reject_once"

TOOL_OPTIONS = [
    PermissionOption(
        option_id=ToolOption.ALLOW_ONCE, name="Allow once", kind=_KIND_ALLOW_ONCE
    ),
    PermissionOption(
        option_id=ToolOption.ALLOW_ALWAYS,
        name="Allow for remainder of this session",
        kind=_KIND_ALLOW_ALWAYS,
    ),
    PermissionOption(
        option_id=ToolOption.ALLOW_ALWAYS_PERMANENT,
        name="Always allow",
        kind=_KIND_ALLOW_ALWAYS,
    ),
    PermissionOption(
        option_id=ToolOption.REJECT_ONCE, name="Deny", kind=_KIND_REJECT_ONCE
    ),
]


def build_permission_options(
    required_permissions: list[RequiredPermission] | None,
) -> list[PermissionOption]:
    """Build ACP permission options, including granular labels when available."""
    if not required_permissions:
        return TOOL_OPTIONS

    permissions_meta = [
        {
            "scope": rp.scope,
            "invocation_pattern": rp.invocation_pattern,
            "session_pattern": rp.session_pattern,
            "label": rp.label,
        }
        for rp in required_permissions
    ]

    return [
        PermissionOption(
            option_id=ToolOption.ALLOW_ONCE, name="Allow once", kind=_KIND_ALLOW_ONCE
        ),
        PermissionOption(
            option_id=ToolOption.ALLOW_ALWAYS,
            name="Allow for remainder of this session",
            kind=_KIND_ALLOW_ALWAYS,
            field_meta={"required_permissions": permissions_meta},
        ),
        PermissionOption(
            option_id=ToolOption.ALLOW_ALWAYS_PERMANENT,
            name="Always allow",
            kind=_KIND_ALLOW_ALWAYS,
            field_meta={"required_permissions": permissions_meta},
        ),
        PermissionOption(
            option_id=ToolOption.REJECT_ONCE, name="Deny", kind=_KIND_REJECT_ONCE
        ),
    ]


def is_valid_acp_mode(profiles: list[AgentProfile], mode_name: str) -> bool:
    return any(
        p.name == mode_name and p.agent_type == AgentType.AGENT for p in profiles
    )


def is_jetbrains_client(client_info: Implementation | None) -> bool:
    return bool(client_info and client_info.name.startswith("JetBrains."))


def build_mode_state(
    profiles: list[AgentProfile], current_mode_id: str
) -> tuple[SessionModeState, SessionConfigOptionSelect]:
    session_modes: list[SessionMode] = []
    config_options: list[SessionConfigSelectOption] = []

    for profile in profiles:
        if profile.agent_type != AgentType.AGENT:
            continue
        session_modes.append(
            SessionMode(
                id=profile.name,
                name=profile.display_name,
                description=profile.description,
            )
        )
        config_options.append(
            SessionConfigSelectOption(
                value=profile.name,
                name=profile.display_name,
                description=profile.description,
            )
        )

    state = SessionModeState(
        current_mode_id=current_mode_id, available_modes=session_modes
    )
    config = SessionConfigOptionSelect(
        id="mode",
        name="Session Mode",
        current_value=current_mode_id,
        category="mode",
        type="select",
        options=config_options,
    )
    return state, config


def build_model_config(
    models: Mapping[str, ModelConfig] | list[ModelConfig], current_model_id: str
) -> SessionConfigOptionSelect:
    model_values = models.values() if isinstance(models, Mapping) else models
    config_options = [
        SessionConfigSelectOption(
            value=model.alias, name=model.alias, description=model.name
        )
        for model in model_values
    ]

    return SessionConfigOptionSelect(
        id="model",
        name="Model",
        current_value=current_model_id,
        category="model",
        type="select",
        options=config_options,
    )


def make_thinking_response(
    current_thinking: ThinkingLevel,
) -> SessionConfigOptionSelect:
    return SessionConfigOptionSelect(
        id="thinking",
        name="Thinking",
        current_value=current_thinking,
        category="thinking",
        type="select",
        options=[
            SessionConfigSelectOption(value=level, name=level.capitalize())
            for level in THINKING_LEVELS
        ],
    )


def create_compact_start_session_update(event: CompactStartEvent) -> ToolCallStart:
    # WORKAROUND: Using tool_call to communicate compact events to the client.
    # This should be revisited when the ACP protocol defines how compact events
    # should be represented.
    # [RFD](https://agentclientprotocol.com/rfds/session-usage)
    return ToolCallStart(
        session_update="tool_call",
        tool_call_id=event.tool_call_id,
        title="Compacting conversation history...",
        kind="other",
        status="in_progress",
        content=[
            ContentToolCallContent(
                type="content",
                content=TextContentBlock(
                    type="text",
                    text="Automatic context management, no approval required. This may take some time...",
                ),
            )
        ],
    )


def create_compact_end_session_update(event: CompactEndEvent) -> ToolCallProgress:
    # WORKAROUND: Using tool_call_update to communicate compact events to the client.
    # This should be revisited when the ACP protocol defines how compact events
    # should be represented.
    # [RFD](https://agentclientprotocol.com/rfds/session-usage)
    return ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id=event.tool_call_id,
        title="Compacted conversation history",
        status="completed",
        content=[
            ContentToolCallContent(
                type="content",
                content=TextContentBlock(
                    type="text",
                    text=(
                        compact_complete_display(
                            old_session_id=event.old_session_id,
                            new_session_id=event.new_session_id,
                        )
                    ),
                ),
            )
        ],
    )


def get_proxy_help_text() -> str:
    lines = [
        "## Proxy Configuration",
        "",
        "Configure proxy and SSL settings for HTTP requests.",
        "",
        "### Usage:",
        "- `/proxy-setup` - Show this help and current settings",
        "- `/proxy-setup KEY value` - Set an environment variable",
        "- `/proxy-setup KEY` - Remove an environment variable",
        "",
        "### Supported Variables:",
    ]

    for key, description in SUPPORTED_PROXY_VARS.items():
        lines.append(f"- `{key}`: {description}")

    lines.extend(["", "### Current Settings:"])

    current = get_current_proxy_settings()
    any_set = False
    for key, value in current.items():
        if value:
            lines.append(f"- `{key}={value}`")
            any_set = True

    if not any_set:
        lines.append("- (none configured)")

    return "\n".join(lines)


def create_user_message_replay(msg: LLMMessage) -> UserMessageChunk:
    content = msg.content if isinstance(msg.content, str) else ""
    field_meta = (
        {
            USER_DISPLAY_CONTENT_META_KEY: msg.user_display_content.model_dump(
                mode="json"
            )
        }
        if msg.user_display_content is not None
        else None
    )
    return UserMessageChunk(
        session_update="user_message_chunk",
        content=TextContentBlock(type="text", text=content),
        message_id=msg.message_id,
        field_meta=field_meta,
    )


def create_assistant_message_replay(msg: LLMMessage) -> AgentMessageChunk | None:
    content = msg.content if isinstance(msg.content, str) else ""
    if not content:
        return None

    return AgentMessageChunk(
        session_update="agent_message_chunk",
        content=TextContentBlock(type="text", text=content),
        message_id=msg.message_id,
    )


def create_reasoning_replay(msg: LLMMessage) -> AgentThoughtChunk | None:
    if not isinstance(msg.reasoning_content, str) or not msg.reasoning_content:
        return None

    return AgentThoughtChunk(
        session_update="agent_thought_chunk",
        content=TextContentBlock(type="text", text=msg.reasoning_content),
        message_id=msg.reasoning_message_id,
    )


def create_tool_call_replay(
    tool_call_id: str, tool_name: str, arguments: str | None
) -> ToolCallStart:
    # Fallback when a stored tool call can't be resolved (unknown tool or
    # args that no longer validate). Replayed calls are historical, so they
    # carry a terminal status; the host drops created events without one.
    return ToolCallStart(
        session_update="tool_call",
        title=tool_name,
        tool_call_id=tool_call_id,
        kind=resolve_kind(tool_name),
        status="completed",
        raw_input=arguments,
        field_meta={"tool_name": tool_name},
    )


def tool_call_replay_update(
    resolved: ResolvedToolCall | None, tool_call: ToolCall
) -> SessionUpdate | None:
    tool_name = tool_call.function.name or ""
    tool_call_id = tool_call.id or ""
    if resolved is None:
        return create_tool_call_replay(
            tool_call_id, tool_name, tool_call.function.arguments
        )

    return tool_call_session_update(
        ToolCallEvent(
            tool_call_id=resolved.call_id,
            tool_name=resolved.tool_name,
            tool_class=resolved.tool_class,
            args=resolved.validated_args,
        ),
        status="completed",
    )


def create_tool_result_replay(msg: LLMMessage) -> ToolCallProgress | None:
    if not msg.tool_call_id:
        return None

    content = msg.content if isinstance(msg.content, str) else ""
    tool_name = msg.name or ""
    return ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id=msg.tool_call_id,
        status="completed",
        kind=resolve_kind(tool_name),
        raw_output=content,
        field_meta={"tool_name": tool_name},
        content=[
            ContentToolCallContent(
                type="content", content=TextContentBlock(type="text", text=content)
            )
        ]
        if content
        else None,
    )
