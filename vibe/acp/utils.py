from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Literal, cast

from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ContentToolCallContent,
    ModelInfo,
    PermissionOption,
    SessionConfigOption,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionMode,
    SessionModelState,
    SessionModeState,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
)

from vibe.core.agents.models import AgentProfile, AgentType
from vibe.core.proxy_setup import SUPPORTED_PROXY_VARS, get_current_proxy_settings
from vibe.core.types import CompactEndEvent, CompactStartEvent, LLMMessage
from vibe.core.utils import compact_reduction_display

if TYPE_CHECKING:
    from vibe.core.config import ModelConfig


class ToolOption(StrEnum):
    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    REJECT_ONCE = "reject_once"
    REJECT_ALWAYS = "reject_always"


TOOL_OPTIONS = [
    PermissionOption(
        option_id=ToolOption.ALLOW_ONCE,
        name="Allow once",
        kind=cast(Literal["allow_once"], ToolOption.ALLOW_ONCE),
    ),
    PermissionOption(
        option_id=ToolOption.ALLOW_ALWAYS,
        name="Allow always",
        kind=cast(Literal["allow_always"], ToolOption.ALLOW_ALWAYS),
    ),
    PermissionOption(
        option_id=ToolOption.REJECT_ONCE,
        name="Reject once",
        kind=cast(Literal["reject_once"], ToolOption.REJECT_ONCE),
    ),
]


def is_valid_acp_mode(profiles: list[AgentProfile], mode_name: str) -> bool:
    return any(
        p.name == mode_name and p.agent_type == AgentType.AGENT for p in profiles
    )


def make_mode_response(
    profiles: list[AgentProfile], current_mode_id: str
) -> tuple[SessionModeState, SessionConfigOption]:
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
    config = SessionConfigOption(
        root=SessionConfigOptionSelect(
            id="mode",
            name="Session Mode",
            current_value=current_mode_id,
            category="mode",
            type="select",
            options=config_options,
        )
    )
    return state, config


def make_model_response(
    models: list[ModelConfig], current_model_id: str
) -> tuple[SessionModelState, SessionConfigOption]:
    model_infos: list[ModelInfo] = []
    config_options: list[SessionConfigSelectOption] = []

    for model in models:
        model_infos.append(ModelInfo(model_id=model.alias, name=model.alias))
        config_options.append(
            SessionConfigSelectOption(
                value=model.alias, name=model.alias, description=model.name
            )
        )

    state = SessionModelState(
        current_model_id=current_model_id, available_models=model_infos
    )
    config_option = SessionConfigOption(
        root=SessionConfigOptionSelect(
            id="model",
            name="Model",
            current_value=current_model_id,
            category="model",
            type="select",
            options=config_options,
        )
    )
    return state, config_option


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
                        compact_reduction_display(
                            event.old_context_tokens, event.new_context_tokens
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
    return UserMessageChunk(
        session_update="user_message_chunk",
        content=TextContentBlock(type="text", text=content),
        field_meta={"messageId": msg.message_id} if msg.message_id else {},
    )


def create_assistant_message_replay(msg: LLMMessage) -> AgentMessageChunk | None:
    content = msg.content if isinstance(msg.content, str) else ""
    if not content:
        return None

    return AgentMessageChunk(
        session_update="agent_message_chunk",
        content=TextContentBlock(type="text", text=content),
        field_meta={"messageId": msg.message_id} if msg.message_id else {},
    )


def create_reasoning_replay(msg: LLMMessage) -> AgentThoughtChunk | None:
    if not isinstance(msg.reasoning_content, str) or not msg.reasoning_content:
        return None

    return AgentThoughtChunk(
        session_update="agent_thought_chunk",
        content=TextContentBlock(type="text", text=msg.reasoning_content),
        field_meta={"messageId": msg.message_id} if msg.message_id else {},
    )


def create_tool_call_replay(
    tool_call_id: str, tool_name: str, arguments: str | None
) -> ToolCallStart:
    return ToolCallStart(
        session_update="tool_call",
        title=tool_name,
        tool_call_id=tool_call_id,
        kind="other",
        raw_input=arguments,
    )


def create_tool_result_replay(msg: LLMMessage) -> ToolCallProgress | None:
    if not msg.tool_call_id:
        return None

    content = msg.content if isinstance(msg.content, str) else ""
    return ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id=msg.tool_call_id,
        status="completed",
        raw_output=content,
        content=[
            ContentToolCallContent(
                type="content", content=TextContentBlock(type="text", text=content)
            )
        ]
        if content
        else None,
    )
