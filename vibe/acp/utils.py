from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Literal, cast

from acp.schema import (
    ContentToolCallContent,
    PermissionOption,
    SessionMode,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
)

from vibe.core.agents.models import AgentProfile, AgentType
from vibe.core.types import CompactEndEvent, CompactStartEvent
from vibe.core.utils import compact_reduction_display

if TYPE_CHECKING:
    from vibe.core.agents.manager import AgentManager


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


def agent_profile_to_acp(profile: AgentProfile) -> SessionMode:
    return SessionMode(
        id=profile.name, name=profile.display_name, description=profile.description
    )


def is_valid_acp_agent(agent_manager: AgentManager, agent_name: str) -> bool:
    return agent_name in agent_manager.available_agents


def get_all_acp_session_modes(agent_manager: AgentManager) -> list[SessionMode]:
    return [
        agent_profile_to_acp(profile)
        for profile in agent_manager.available_agents.values()
        if profile.agent_type == AgentType.AGENT
    ]


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
