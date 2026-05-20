from __future__ import annotations

from abc import abstractmethod
from typing import Annotated, cast

from acp import Client
from pydantic import BaseModel, ConfigDict, Field, SkipValidation

from vibe.core.tools.base import BaseTool, ToolError
from vibe.core.tools.manager import ToolManager


class AcpToolState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    client: Annotated[Client | None, SkipValidation] = Field(
        default=None, description="ACP Client"
    )
    session_id: str | None = Field(default=None, description="Current ACP session ID")


class BaseAcpTool[ToolState: AcpToolState](BaseTool):
    state: ToolState

    @classmethod
    def get_tool_instance(
        cls, tool_name: str, tool_manager: ToolManager
    ) -> BaseAcpTool[AcpToolState]:
        return cast(BaseAcpTool[AcpToolState], tool_manager.get(tool_name))

    @classmethod
    def update_tool_state(
        cls, *, tool_manager: ToolManager, client: Client | None, session_id: str | None
    ) -> None:
        tool_instance = cls.get_tool_instance(cls.get_name(), tool_manager)
        tool_instance.state.client = client
        tool_instance.state.session_id = session_id

    @classmethod
    @abstractmethod
    def _get_tool_state_class(cls) -> type[ToolState]: ...

    def _load_state(self) -> tuple[Client, str]:
        if self.state.client is None:
            raise ToolError(
                "Client not available in tool state. This tool can only be used within an ACP session."
            )
        if self.state.session_id is None:
            raise ToolError(
                "Session ID not available in tool state. This tool can only be used within an ACP session."
            )

        return self.state.client, self.state.session_id
