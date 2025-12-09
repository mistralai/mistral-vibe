from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, cast, runtime_checkable

from acp import AgentSideConnection, SessionNotification
from acp.helpers import SessionUpdate, ToolCallContentVariant
from acp.schema import ToolCallProgress
from pydantic import Field

from vibe.core.tools.base import BaseTool, ToolError
from vibe.core.tools.manager import ToolManager
from vibe.core.types import ToolCallEvent, ToolResultEvent
from vibe.core.utils import logger


@runtime_checkable
class ToolCallSessionUpdateProtocol(Protocol):
    @classmethod
    def tool_call_session_update(cls, event: ToolCallEvent) -> SessionUpdate | None: ...


@runtime_checkable
class ToolResultSessionUpdateProtocol(Protocol):
    @classmethod
    def tool_result_session_update(
        cls, event: ToolResultEvent
    ) -> SessionUpdate | None: ...


class AcpToolState:
    connection: AgentSideConnection | None = Field(
        default=None, description="ACP agent-side connection"
    )
    session_id: str | None = Field(default=None, description="Current ACP session ID")
    tool_call_id: str | None = Field(
        default=None, description="Current ACP tool call ID"
    )


class BaseAcpTool[ToolState: AcpToolState](BaseTool):
    state: ToolState

    @classmethod
    def get_tool_instance(
        cls, tool_name: str, tool_manager: ToolManager
    ) -> BaseAcpTool[AcpToolState]:
        return cast(BaseAcpTool[AcpToolState], tool_manager.get(tool_name))

    @classmethod
    def update_tool_state(
        cls,
        *,
        tool_manager: ToolManager,
        connection: AgentSideConnection | None,
        session_id: str | None,
        tool_call_id: str | None,
    ) -> None:
        tool_instance = cls.get_tool_instance(cls.get_name(), tool_manager)
        tool_instance.state.connection = connection
        tool_instance.state.session_id = session_id
        tool_instance.state.tool_call_id = tool_call_id

    @classmethod
    @abstractmethod
    def _get_tool_state_class(cls) -> type[ToolState]: ...

    def _load_state(self) -> tuple[AgentSideConnection, str, str | None]:
        if self.state.connection is None:
            raise ToolError(
                "Connection not available in tool state. This tool can only be used within an ACP session."
            )
        if self.state.session_id is None:
            raise ToolError(
                "Session ID not available in tool state. This tool can only be used within an ACP session."
            )

        return self.state.connection, self.state.session_id, self.state.tool_call_id

    async def _send_in_progress_session_update(
        self, content: list[ToolCallContentVariant] | None = None
    ) -> None:
        connection, session_id, tool_call_id = self._load_state()
        if tool_call_id is None:
            return

        try:
            await connection.sessionUpdate(
                SessionNotification(
                    sessionId=session_id,
                    update=ToolCallProgress(
                        sessionUpdate="tool_call_update",
                        toolCallId=tool_call_id,
                        status="in_progress",
                        content=content,
                    ),
                )
            )
        except Exception as e:
            logger.error(f"Failed to update session: {e!r}")
