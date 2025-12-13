from __future__ import annotations

from enum import StrEnum
from typing import Literal, cast

from acp.schema import PermissionOption, SessionMode

from vibe.core.modes import MODE_CONFIGS, AgentMode


class ToolOption(StrEnum):
    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    REJECT_ONCE = "reject_once"
    REJECT_ALWAYS = "reject_always"


TOOL_OPTIONS = [
    PermissionOption(
        optionId=ToolOption.ALLOW_ONCE,
        name="Allow once",
        kind=cast(Literal["allow_once"], ToolOption.ALLOW_ONCE),
    ),
    PermissionOption(
        optionId=ToolOption.ALLOW_ALWAYS,
        name="Allow always",
        kind=cast(Literal["allow_always"], ToolOption.ALLOW_ALWAYS),
    ),
    PermissionOption(
        optionId=ToolOption.REJECT_ONCE,
        name="Reject once",
        kind=cast(Literal["reject_once"], ToolOption.REJECT_ONCE),
    ),
]


def agent_mode_to_acp(mode: AgentMode) -> SessionMode:
    config = MODE_CONFIGS[mode]
    return SessionMode(
        id=mode.value, name=config.display_name, description=config.description
    )


def acp_to_agent_mode(mode_id: str) -> AgentMode | None:
    return AgentMode.from_string(mode_id)


def is_valid_acp_mode(mode_id: str) -> bool:
    return AgentMode.from_string(mode_id) is not None


def get_all_acp_session_modes() -> list[SessionMode]:
    return [agent_mode_to_acp(mode) for mode in AgentMode]
