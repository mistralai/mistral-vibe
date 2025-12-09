from __future__ import annotations

import enum
from enum import StrEnum
from typing import Literal, cast

from acp.schema import PermissionOption, SessionMode


class VibeSessionMode(enum.StrEnum):
    APPROVAL_REQUIRED = enum.auto()
    AUTO_APPROVE = enum.auto()

    def to_acp_session_mode(self) -> SessionMode:
        match self:
            case self.APPROVAL_REQUIRED:
                return SessionMode(
                    id=VibeSessionMode.APPROVAL_REQUIRED,
                    name="Approval Required",
                    description="Requires user approval for tool executions",
                )
            case self.AUTO_APPROVE:
                return SessionMode(
                    id=VibeSessionMode.AUTO_APPROVE,
                    name="Auto Approve",
                    description="Automatically approves all tool executions",
                )

    @classmethod
    def from_acp_session_mode(cls, session_mode: SessionMode) -> VibeSessionMode | None:
        if not cls.is_valid(session_mode.id):
            return None
        return cls(session_mode.id)

    @classmethod
    def is_valid(cls, mode_id: str) -> bool:
        try:
            return cls(mode_id).to_acp_session_mode() is not None
        except (ValueError, KeyError):
            return False

    @classmethod
    def get_all_acp_session_modes(cls) -> list[SessionMode]:
        return [mode.to_acp_session_mode() for mode in cls]


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
